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
HOST = '192.168.0.207'
CHUNK = 1024
FORMAT = pyaudio.paInt16
CHANNELS = 2
RATE = 44100
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
        # Update at most every 0.1 seconds to avoid flooding the console
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
            # Clear using xsel
            subprocess.run(['xsel', '-b', '-c'], check=True)
            # Clear using xclip as backup
            subprocess.run(['xclip', '-selection', 'clipboard', '-i'], input=b'', check=True)
        except Exception as e:
            logging.error(f"Error clearing clipboard: {e}")

    def _cleanup_old_files(self):
        """Clean up all files in the ClipboardSync directory"""
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
        """
        Set file URIs to clipboard using multiple methods for compatibility
        """
        try:
            # Format paths as URIs
            uri_list = '\n'.join([f"file://{path}" for path in file_paths])
            
            # Set with xclip for URI list
            subprocess.run(['xclip', '-selection', 'clipboard', '-t', 'text/uri-list', '-i'], 
                         input=uri_list.encode(), check=True)
            
            # Set text format as backup
            subprocess.run(['xclip', '-selection', 'clipboard', '-i'], 
                         input=uri_list.encode(), check=True)
            
            # Set with xsel as additional backup
            subprocess.run(['xsel', '-b', '-i'], input=uri_list.encode(), check=True)
            
            # Set GNOME format
            gnome_format = f"copy\n{uri_list}"
            subprocess.run(['xclip', '-selection', 'clipboard', 
                          '-t', 'x-special/gnome-copied-files', '-i'], 
                         input=gnome_format.encode(), check=True)

            logging.info(f"Set clipboard with files: {', '.join(file_paths)}")
            
        except Exception as e:
            logging.error(f"Error setting file to clipboard: {e}")
            # Try basic xclip as last resort
            try:
                paths_text = '\n'.join(file_paths)
                subprocess.run(['xclip', '-selection', 'clipboard', '-i'], 
                             input=paths_text.encode(), check=True)
            except Exception as e2:
                logging.error(f"Final clipboard attempt failed: {e2}")

    def get_clipboard_content(self):
        """Get current clipboard content"""
        try:
            # Try text first
            text_data = subprocess.run(['xsel', '-b', '-o'], capture_output=True, text=True).stdout.strip()
            if text_data:
                return {'type': 'text', 'data': text_data}
            
            # Try files (using xclip as backup)
            try:
                file_data = subprocess.run(['xclip', '-selection', 'clipboard', '-t', 'text/uri-list', '-o'], 
                                         capture_output=True, text=True).stdout
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
        """Set clipboard content"""
        try:
            # Clear existing clipboard content first
            self._clear_clipboard()
            
            if content['type'] == 'text':
                # Try xsel first
                try:
                    process = subprocess.Popen(['xsel', '-b', '-i'], stdin=subprocess.PIPE)
                    process.communicate(input=content['data'].encode())
                except:
                    # Fallback to xclip
                    process = subprocess.Popen(['xclip', '-selection', 'clipboard', '-i'], stdin=subprocess.PIPE)
                    process.communicate(input=content['data'].encode())
                
                logging.info("Text content set to clipboard")
                
            elif content['type'] == 'files':
                # Clean up old files first
                self._cleanup_old_files()
                
                saved_paths = []
                for file_info in content['files']:
                    target_path = self.clipboard_dir / file_info['name']
                    with open(target_path, 'wb') as f:
                        f.write(base64.b64decode(file_info['data']))
                    saved_paths.append(str(target_path.absolute()))
                
                # Set the saved files to clipboard
                if saved_paths:
                    self._set_file_to_clipboard(saved_paths)
                    logging.info(f"Files saved to: {self.clipboard_dir} and added to clipboard")
                
        except Exception as e:
            logging.error(f"Error setting clipboard: {e}")
            # Try to clear clipboard on error
            self._clear_clipboard()

class UnifiedClient:
    def __init__(self):
        self.running = False
        self.clipboard_handler = ClipboardHandler()  # Changed from self.clipboard to self.clipboard_handler
        self.p = pyaudio.PyAudio()
        self.audio_streams = {'input': None, 'output': None}
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

    def handle_file_transfer(self, conn, file_info):
        try:
            with open(file_info['path'], 'rb') as f:
                total_size = file_info['size']
                sent_size = 0
                
                # Send file info first
                header = json.dumps({
                    'type': 'file_transfer',
                    'name': file_info['name'],
                    'size': total_size
                })
                conn.send(f"{len(header)}:".encode() + header.encode())
                
                # Create progress bar
                progress = ProgressBar(total_size, prefix=f'Sending {file_info["name"]}:', 
                                    suffix='Complete', length=50)
                
                while sent_size < total_size:
                    chunk = f.read(MAX_CHUNK_SIZE)
                    if not chunk:
                        break
                    
                    # Compress chunk
                    compressed = zlib.compress(chunk)
                    size_header = f"{len(compressed)}:".encode()
                    conn.send(size_header + compressed)
                    
                    sent_size += len(chunk)
                    progress.update(sent_size)
                    
        except Exception as e:
            logging.error(f"Error sending file {file_info['name']}: {e}")

    def _receive_file(self, conn, file_info):
        try:
            # Clean up old files before receiving new ones
            self.clipboard_handler._cleanup_old_files()  # Updated to use clipboard_handler
            
            file_path = self.clipboard_handler.clipboard_dir / file_info['name']  # Updated to use clipboard_handler
            received_size = 0
            total_size = file_info['size']
            saved_paths = []
            
            # Create progress bar
            progress = ProgressBar(total_size, prefix=f'Receiving {file_info["name"]}:', 
                                suffix='Complete', length=50)
            
            with open(file_path, 'wb') as f:
                while received_size < total_size:
                    header = ""
                    while ":" not in header:
                        char = conn.recv(1).decode()
                        if not char:
                            raise ConnectionError
                        header += char
                    
                    chunk_size = int(header.strip(":"))
                    chunk = b""
                    while len(chunk) < chunk_size:
                        part = conn.recv(chunk_size - len(chunk))
                        if not part:
                            raise ConnectionError
                        chunk += part
                    
                    decompressed = zlib.decompress(chunk)
                    f.write(decompressed)
                    
                    received_size += len(decompressed)
                    progress.update(received_size)
            
            saved_paths.append(str(file_path.absolute()))
            
            # Set the received files to clipboard
            if saved_paths:
                self.clipboard_handler._set_file_to_clipboard(saved_paths)  # Updated to use clipboard_handler
            
        except Exception as e:
            logging.error(f"Error receiving file {file_info['name']}: {e}")

    def start_clipboard_sync(self):
        while self.running:
            try:
                logging.info(f"Connecting to clipboard service {HOST}:{CLIPBOARD_PORT}...")
                clipboard_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                clipboard_socket.connect((HOST, CLIPBOARD_PORT))
                logging.info("Clipboard sync connected!")
                
                while self.running:
                    # Check local clipboard for changes
                    current = self.clipboard_handler.get_clipboard_content()  # Updated to use clipboard_handler
                    if current and current != self.clipboard_handler.last_content:  # Updated to use clipboard_handler
                        if current['type'] == 'files':
                            for file_info in current['files']:
                                self.handle_file_transfer(clipboard_socket, file_info)
                        else:
                            data = json.dumps(current)
                            clipboard_socket.send(f"{len(data)}:".encode() + data.encode())
                        self.clipboard_handler.last_content = current  # Updated to use clipboard_handler
                    
                    # Check for incoming data
                    try:
                        clipboard_socket.settimeout(0.1)
                        header = ""
                        while ":" not in header:
                            char = clipboard_socket.recv(1).decode()
                            if not char:
                                raise ConnectionError
                            header += char
                        
                        size = int(header.strip(":"))
                        data = clipboard_socket.recv(size).decode()
                        content = json.loads(data)
                        
                        if content['type'] == 'file_transfer':
                            self._receive_file(clipboard_socket, content)
                        else:
                            self.clipboard_handler.set_clipboard_content(content)  # Updated to use clipboard_handler
                            self.clipboard_handler.last_content = content  # Updated to use clipboard_handler
                            
                    except socket.timeout:
                        pass
                        
                    time.sleep(0.1)
                    
            except Exception as e:
                logging.error(f"Clipboard connection error: {e}")
                time.sleep(5)  # Wait before reconnecting
                
            finally:
                clipboard_socket.close()

    def start_audio_sync(self):
        while self.running:
            try:
                logging.info(f"Connecting to audio service {HOST}:{AUDIO_PORT}...")
                audio_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                audio_socket.connect((HOST, AUDIO_PORT))
                logging.info("Audio sync connected!")
                
                def receive_audio():
                    while self.running:
                        try:
                            data = audio_socket.recv(CHUNK * 4)
                            if data and self.audio_streams['output']:
                                self.audio_streams['output'].write(data)
                        except:
                            break
                            
                def send_audio():
                    while self.running:
                        try:
                            if self.audio_streams['input']:
                                data = self.audio_streams['input'].read(CHUNK, exception_on_overflow=False)
                                audio_socket.send(data)
                        except:
                            break
                            
                def send_audio():
                    while self.running:
                        try:
                            if self.audio_streams['input']:
                                data = self.audio_streams['input'].read(CHUNK, exception_on_overflow=False)
                                audio_socket.send(data)
                        except:
                            break
                
                receive_thread = threading.Thread(target=receive_audio)
                send_thread = threading.Thread(target=send_audio)
                
                receive_thread.start()
                send_thread.start()
                
                receive_thread.join()
                send_thread.join()
                
            except Exception as e:
                logging.error(f"Audio connection error: {e}")
                time.sleep(5)  # Wait before reconnecting
                
            finally:
                audio_socket.close()

    def start(self):
        self.running = True
        self.setup_audio()
        
        clipboard_thread = threading.Thread(target=self.start_clipboard_sync)
        audio_thread = threading.Thread(target=self.start_audio_sync)
        
        clipboard_thread.start()
        audio_thread.start()
        
        try:
            while self.running:
                time.sleep(0.1)
        except KeyboardInterrupt:
            self.running = False
            
        clipboard_thread.join()
        audio_thread.join()
        
        # Cleanup
        for stream in self.audio_streams.values():
            if stream:
                stream.stop_stream()
                stream.close()
        self.p.terminate()

if __name__ == "__main__":
    client = UnifiedClient()
    client.start()
