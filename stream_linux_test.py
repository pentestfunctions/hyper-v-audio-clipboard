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
HOST = '0.0.0.0'  # Listen on all interfaces
CLIPBOARD_PORT = 5000
AUDIO_PORT = 5001
MAX_CHUNK_SIZE = 1024 * 1024  # 1MB chunks for large files

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
                subprocess.run(['xclip', '-selection', 'clipboard', '-i'], 
                             input=paths_text.encode('utf-8'), check=True)
            except Exception as e2:
                logging.error(f"Final clipboard attempt failed: {e2}")

    def get_clipboard_content(self):
        try:
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
                        with open(path, 'rb') as f:
                            file_data = base64.b64encode(f.read()).decode('utf-8')
                        file_info = {
                            'name': os.path.basename(path),
                            'size': os.path.getsize(path),
                            'data': file_data
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
                subprocess.run(['xsel', '-b', '-i'], input=content['data'].encode('utf-8'), check=True)
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

class AudioServer:
    def __init__(self):
        self.p = pyaudio.PyAudio()
        self.streams = {}
        self.clients = set()
        self.running = False
        self.audio_lock = threading.Lock()
        
    def setup_stream(self, client_id):
        with self.audio_lock:
            if client_id not in self.streams:
                self.streams[client_id] = {
                    'input': self.p.open(
                        format=FORMAT,
                        channels=CHANNELS,
                        rate=RATE,
                        input=True,
                        frames_per_buffer=CHUNK
                    ),
                    'output': self.p.open(
                        format=FORMAT,
                        channels=CHANNELS,
                        rate=RATE,
                        output=True,
                        frames_per_buffer=CHUNK
                    )
                }

    def cleanup_stream(self, client_id):
        with self.audio_lock:
            if client_id in self.streams:
                for stream in self.streams[client_id].values():
                    if stream:
                        stream.stop_stream()
                        stream.close()
                del self.streams[client_id]

    def cleanup_all(self):
        with self.audio_lock:
            for client_id in list(self.streams.keys()):
                self.cleanup_stream(client_id)
            self.p.terminate()

    def handle_client(self, client_socket, client_address):
        client_id = f"{client_address[0]}:{client_address[1]}"
        self.clients.add(client_socket)
        self.setup_stream(client_id)
        
        def receive_audio():
            while self.running and client_socket in self.clients:
                try:
                    data = client_socket.recv(CHUNK * 4)
                    if not data:
                        break
                    
                    # Broadcast to other clients
                    for other_client in self.clients:
                        if other_client != client_socket:
                            try:
                                other_client.send(data)
                            except:
                                pass
                                
                    # Play locally
                    if client_id in self.streams:
                        self.streams[client_id]['output'].write(data)
                except:
                    break

        def send_audio():
            while self.running and client_socket in self.clients:
                try:
                    if client_id in self.streams:
                        data = self.streams[client_id]['input'].read(CHUNK, exception_on_overflow=False)
                        client_socket.send(data)
                except:
                    break

        receive_thread = threading.Thread(target=receive_audio)
        send_thread = threading.Thread(target=send_audio)
        
        receive_thread.start()
        send_thread.start()
        
        receive_thread.join()
        send_thread.join()
        
        self.clients.remove(client_socket)
        self.cleanup_stream(client_id)
        client_socket.close()

class UnifiedServer:
    def __init__(self):
        self.running = False
        self.clipboard_handler = ClipboardHandler()
        self.audio_server = AudioServer()
        self.clients = set()
        logging.basicConfig(
            level=logging.INFO,
            format='%(asctime)s - %(levelname)s - %(message)s',
            datefmt='%Y-%m-%d %H:%M:%S'
        )

    def get_local_ip(self):
        """Get all local IP addresses"""
        ips = []
        try:
            # Get all network interfaces
            interfaces = socket.getaddrinfo(
                host=socket.gethostname(),
                port=None,
                family=socket.AF_INET
            )
            # Extract unique IPs
            ips = sorted(list(set(ip[4][0] for ip in interfaces if not ip[4][0].startswith('127.'))))
        except Exception as e:
            logging.error(f"Error getting local IPs: {e}")
            ips = ['127.0.0.1']
        return ips

    def handle_clipboard_client(self, client_socket, address):
        logging.info(f"New clipboard connection from {address}")
        self.clients.add(client_socket)
        
        try:
            while self.running:
                # Check local clipboard for changes
                current = self.clipboard_handler.get_clipboard_content()
                if current and current != self.clipboard_handler.last_content:
                    data = json.dumps(current)
                    # Broadcast to all clients
                    for client in self.clients:
                        try:
                            client.send(f"{len(data)}:".encode() + data.encode())
                        except:
                            pass
                    self.clipboard_handler.last_content = current
                
                # Check for incoming data
                try:
                    client_socket.settimeout(0.1)
                    header = ""
                    while ":" not in header:
                        char = client_socket.recv(1).decode()
                        if not char:
                            raise ConnectionError
                        header += char
                    
                    size = int(header.strip(":"))
                    data = client_socket.recv(size).decode()
                    content = json.loads(data)
                    
                    if content.get('type'):
                        self.clipboard_handler.set_clipboard_content(content)
                        self.clipboard_handler.last_content = content
                        
                        # Broadcast to other clients
                        for other_client in self.clients:
                            if other_client != client_socket:
                                try:
                                    other_client.send(f"{len(data)}:".encode() + data.encode())
                                except:
                                    pass
                            
                except socket.timeout:
                    pass
                    
                time.sleep(0.1)
                
        except Exception as e:
            logging.error(f"Clipboard client error: {e}")
        finally:
            self.clients.remove(client_socket)
            client_socket.close()

def start(self):
        self.running = True
        self.audio_server.running = True
        
        # Start clipboard server
        clipboard_server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        clipboard_server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        clipboard_server.bind((HOST, CLIPBOARD_PORT))
        clipboard_server.listen(5)
        
        # Start audio server
        audio_server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        audio_server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        audio_server.bind((HOST, AUDIO_PORT))
        audio_server.listen(5)
        
        local_ips = self.get_local_ip()
        logging.info("Server started on:")
        for ip in local_ips:
            logging.info(f"  {ip} - Clipboard port: {CLIPBOARD_PORT}, Audio port: {AUDIO_PORT}")
        
        def handle_clipboard_connections():
            while self.running:
                try:
                    client_socket, address = clipboard_server.accept()
                    thread = threading.Thread(target=self.handle_clipboard_client, 
                                           args=(client_socket, address))
                    thread.start()
                except Exception as e:
                    if self.running:
                        logging.error(f"Clipboard server error: {e}")

        def handle_audio_connections():
            while self.running:
                try:
                    client_socket, address = audio_server.accept()
                    thread = threading.Thread(target=self.audio_server.handle_client,
                                           args=(client_socket, address))
                    thread.start()
                except Exception as e:
                    if self.running:
                        logging.error(f"Audio server error: {e}")

        clipboard_thread = threading.Thread(target=handle_clipboard_connections)
        audio_thread = threading.Thread(target=handle_audio_connections)
        
        clipboard_thread.start()
        audio_thread.start()
        
        try:
            while self.running:
                time.sleep(0.1)
        except KeyboardInterrupt:
            logging.info("Shutting down server...")
            self.running = False
            self.audio_server.running = False
            
        # Cleanup
        for client in self.clients:
            try:
                client.close()
            except:
                pass
                
        clipboard_server.close()
        audio_server.close()
        self.audio_server.cleanup_all()
        
        clipboard_thread.join()
        audio_thread.join()
        
        logging.info("Server shutdown complete")

if __name__ == "__main__":
    server = UnifiedServer()
    server.start()
