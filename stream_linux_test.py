#!/usr/bin/env python3
import socket
import subprocess
import threading
import time
import base64
import os
import json
import pyaudio
import logging
from pathlib import Path
import zlib
import sys
from datetime import datetime

# Configuration
CHUNK = 1024
FORMAT = pyaudio.paInt16
CHANNELS = 2
RATE = 44100
CLIPBOARD_PORT = 5000
AUDIO_PORT = 5001
MAX_CHUNK_SIZE = 1024 * 1024  # 1MB chunks for large files

def get_local_ip():
    """Get local IP address"""
    try:
        # Create a socket to get local IP
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        # Doesn't need to be reachable
        s.connect(('10.255.255.255', 1))
        IP = s.getsockname()[0]
        s.close()
        return IP
    except Exception:
        return '127.0.0.1'

class ProgressBar:
    def __init__(self, total, prefix='', suffix='', decimals=1, length=50, fill='█', print_end="\r"):
        self.total = total
        self.prefix = prefix
        self.suffix = suffix
        self.decimals = decimals
        self.length = length
        self.fill = fill
        self.print_end = print_end
        self.current = 0
        self.last_update = 0
        self._update_progress(0)

    def update(self, current):
        self.current = current
        current_time = time.time()
        if current_time - self.last_update > 0.1 or current >= self.total:
            self._update_progress(current)
            self.last_update = current_time

    def _update_progress(self, current):
        percent = f"{100 * (current / float(self.total)):.{self.decimals}f}"
        filled_length = int(self.length * current // self.total)
        bar = self.fill * filled_length + '-' * (self.length - filled_length)
        sys.stdout.write(f'\r{self.prefix} |{bar}| {percent}% {self.suffix}')
        if current >= self.total:
            sys.stdout.write('\n')
        sys.stdout.flush()

class ClipboardHandler:
    def __init__(self):
        self.last_content = None
        self.clipboard_dir = Path.home() / 'ClipboardSync'
        self.clipboard_dir.mkdir(exist_ok=True)
        
    def _clear_clipboard(self):
        try:
            if os.name == 'nt':  # Windows
                subprocess.run(['cmd', '/c', 'echo off | clip'], check=True)
            else:  # Linux/Unix
                subprocess.run(['xsel', '-b', '-c'], check=True)
                subprocess.run(['xclip', '-selection', 'clipboard', '-i'], input=b'', check=True)
        except Exception as e:
            logging.error(f"Error clearing clipboard: {e}")

    def _cleanup_old_files(self):
        try:
            for file_path in self.clipboard_dir.glob('*'):
                try:
                    if file_path.is_file():
                        file_path.unlink()
                except Exception as e:
                    logging.error(f"Error deleting file {file_path}: {e}")
        except Exception as e:
            logging.error(f"Error cleaning up directory: {e}")

    def _set_file_to_clipboard(self, file_paths):
        try:
            if os.name == 'nt':  # Windows
                # PowerShell command to set files to clipboard
                ps_script = f"Set-Clipboard -Path {','.join([f"'{path}'" for path in file_paths])}"
                subprocess.run(['powershell', '-Command', ps_script], check=True)
            else:  # Linux/Unix
                uri_list = '\n'.join([f"file://{path}" for path in file_paths])
                
                subprocess.run(['xclip', '-selection', 'clipboard', '-t', 'text/uri-list', '-i'], 
                             input=uri_list.encode('utf-8'), check=True)
                
                subprocess.run(['xclip', '-selection', 'clipboard', '-i'], 
                             input=uri_list.encode('utf-8'), check=True)
                
                subprocess.run(['xsel', '-b', '-i'], input=uri_list.encode('utf-8'), check=True)
                
                gnome_format = f"copy\n{uri_list}"
                subprocess.run(['xclip', '-selection', 'clipboard', 
                              '-t', 'x-special/gnome-copied-files', '-i'], 
                             input=gnome_format.encode('utf-8'), check=True)

            logging.info(f"Set clipboard with files: {', '.join(file_paths)}")
            
        except Exception as e:
            logging.error(f"Error setting file to clipboard: {e}")
            try:
                paths_text = '\n'.join(file_paths)
                if os.name == 'nt':
                    subprocess.run(['clip'], input=paths_text.encode('utf-8'), check=True)
                else:
                    subprocess.run(['xclip', '-selection', 'clipboard', '-i'], 
                                 input=paths_text.encode('utf-8'), check=True)
            except Exception as e2:
                logging.error(f"Final clipboard attempt failed: {e2}")

    def get_clipboard_content(self):
        try:
            if os.name == 'nt':  # Windows
                try:
                    # Try to get clipboard content using PowerShell
                    ps_script = """
                    Add-Type -AssemblyName System.Windows.Forms
                    $clipboard = [System.Windows.Forms.Clipboard]::GetDataObject()
                    if ($clipboard.GetDataPresent([System.Windows.Forms.DataFormats]::FileDrop)) {
                        $files = $clipboard.GetData([System.Windows.Forms.DataFormats]::FileDrop)
                        $files -join "`n"
                    } else {
                        [System.Windows.Forms.Clipboard]::GetText()
                    }
                    """
                    result = subprocess.run(['powershell', '-Command', ps_script], 
                                         capture_output=True, text=True, encoding='utf-8')
                    if result.stdout.strip():
                        # Check if it's file paths
                        lines = result.stdout.strip().split('\n')
                        if all(os.path.exists(line.strip()) for line in lines):
                            return self._process_files('\n'.join([f"file://{line.strip()}" for line in lines]))
                        return {'type': 'text', 'data': result.stdout.strip()}
                except Exception as e:
                    logging.error(f"PowerShell clipboard error: {e}")
                    return None
            else:  # Linux/Unix
                text_data = subprocess.run(['xsel', '-b', '-o'], 
                                         capture_output=True, text=True, encoding='utf-8').stdout.strip()
                if text_data:
                    return {'type': 'text', 'data': text_data}
                
                try:
                    file_data = subprocess.run(['xclip', '-selection', 'clipboard', '-t', 'text/uri-list', '-o'], 
                                             capture_output=True, text=True, encoding='utf-8').stdout
                    if file_data:
                        return self._process_files(file_data)
                except:
                    pass
                    
        except Exception as e:
            logging.error(f"Clipboard error: {e}")
        return None

    def _process_files(self, file_data):
        processed_files = []
        for uri in file_data.strip().split('\n'):
            if uri.startswith('file://'):
                path = uri[7:].strip()
                if os.path.exists(path):
                    try:
                        file_info = {
                            'name': os.path.basename(path),
                            'size': os.path.getsize(path),
                            'path': path
                        }
                        processed_files.append(file_info)
                    except Exception as e:
                        logging.error(f"Error processing file {path}: {e}")
        
        if processed_files:
            return {'type': 'files', 'files': processed_files}
        return None

    def set_clipboard_content(self, content):
        try:
            self._clear_clipboard()
            
            if content['type'] == 'text':
                if os.name == 'nt':  # Windows
                    subprocess.run(['clip'], input=content['data'].encode('utf-8'), check=True)
                else:  # Linux/Unix
                    try:
                        subprocess.run(['xsel', '-b', '-i'], 
                                     input=content['data'].encode('utf-8'), check=True)
                    except:
                        subprocess.run(['xclip', '-selection', 'clipboard', '-i'], 
                                     input=content['data'].encode('utf-8'), check=True)
                
                logging.info("Text content set to clipboard")
                
            elif content['type'] == 'files':
                self._cleanup_old_files()
                
                saved_paths = []
                for file_info in content['files']:
                    target_path = self.clipboard_dir / file_info['name']
                    with open(target_path, 'wb') as f:
                        f.write(base64.b64decode(file_info['data']))
                    saved_paths.append(str(target_path.absolute()))
                
                if saved_paths:
                    self._set_file_to_clipboard(saved_paths)
                    logging.info(f"Files saved to: {self.clipboard_dir} and added to clipboard")
                
        except Exception as e:
            logging.error(f"Error setting clipboard: {e}")
            self._clear_clipboard()

class UnifiedServer:
    def __init__(self):
        self.running = False
        self.clipboard_handler = ClipboardHandler()
        self.p = pyaudio.PyAudio()
        self.audio_streams = {'input': None, 'output': None}
        self.clients = set()
        logging.basicConfig(
            level=logging.INFO,
            format='%(asctime)s - %(levelname)s - %(message)s',
            datefmt='%Y-%m-%d %H:%M:%S'
        )

    def setup_audio(self):
        self.audio_streams['input'] = self.p.open(
            format=FORMAT,
            channels=CHANNELS,
            rate=RATE,
            input=True,
            frames_per_buffer=CHUNK
        )
        
        self.audio_streams['output'] = self.p.open(
            format=FORMAT,
            channels=CHANNELS,
            rate=RATE,
            output=True,
            frames_per_buffer=CHUNK
        )

    def handle_client(self, client_socket, address):
        logging.info(f"New connection from {address}")
        self.clients.add(client_socket)
        
        try:
            while self.running:
                try:
                    client_socket.settimeout(0.1)
                    header = ""
                    while ":" not in header:
                        char = client_socket.recv(1).decode()
                        if not char:
                            raise ConnectionError
                        header += char
                    
                    size = int(header.strip(":"))
                    data = client_socket.recv(size).decode('utf-8')
                    content = json.loads(data)
                    
                    if content['type'] == 'file_transfer':
                        self._receive_file(client_socket, content)
                    else:
                        self.clipboard_handler.set_clipboard_content(content)
                        self.clipboard_handler.last_content = content
                        
                        # Broadcast to other clients
                        self._broadcast(client_socket, data)
                        
                except socket.timeout:
                    # Check local clipboard for changes
                    current = self.clipboard_handler.get_clipboard_content()
                    if current and current != self.clipboard_handler.last_content:
                        if current['type'] == 'files':
                            for file_info in current['files']:
                                self.handle_file_transfer(client_socket, file_info)
                        else:
                            data = json.dumps(current)
                            client_socket.send(f"{len(data)}:".encode() + data.encode('utf-8'))
                        self.clipboard_handler.last_content = current
                    
                    time.sleep(0.1)
                    
        except Exception as e:
            logging.error(f"Client error: {e}")
        finally:
            self.clients.remove(client_socket)
            client_socket.close()

    def _broadcast(self, sender, data):
        """Broadcast data to all clients except sender"""
        for client in self.clients:
            if client != sender:
                try:
                    client.send(f"{len(data)}:".encode() + data.encode('utf-8'))
                except Exception as e:
                    logging.error(f"Broadcast error: {e}")

    def start(self):
        self.running = True
        self.setup_audio()
        
        # Get local IP
        local_ip = get_local_ip()
        
        # Start clipboard server
        clipboard_server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        clipboard_server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        clipboard_server.bind((local_ip, CLIPBOARD_PORT))
        clipboard_server.listen(5)
        
        logging.info(f"Server started on {local_ip}")
        logging.info(f"Clipboard service listening on port {CLIPBOARD_PORT}")
        
        try:
            while self.running:
                try:
                    clipboard_server.settimeout(1)
                    client_socket, address = clipboard_server.accept()
                    client_thread = threading.Thread(target=self.handle_client, 
                                                  args=(client_socket, address))
                    client_thread.start()
                except socket.timeout:
                    continue
                    
        except KeyboardInterrupt:
            self.running = False
            
        finally:
            # Cleanup
            clipboard_server.close()
            for client in self.clients:
                client.close()
            
            for stream in self.audio_streams.values():
                if stream:
                    stream.stop_stream()
                    stream.close()
            self.p.terminate()

if __name__ == "__main__":
    server = UnifiedServer()
    server.start()
