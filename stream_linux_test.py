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
import sys

# Configuration
CHUNK = 1024
FORMAT = pyaudio.paInt16
CHANNELS = 2
RATE = 44100
HOST = '0.0.0.0'  # Listen on all interfaces
CLIPBOARD_PORT = 5000
AUDIO_PORT = 5001
MAX_CHUNK_SIZE = 1024 * 1024  # 1MB chunks for large files

class ClipboardHandler:
    def __init__(self):
        self.last_content = None
        self.clipboard_dir = Path.home() / 'ClipboardSync'
        self.clipboard_dir.mkdir(exist_ok=True)
        self.clients = set()
        
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
            
            # Set with xclip for URI list
            subprocess.run(['xclip', '-selection', 'clipboard', '-t', 'text/uri-list', '-i'], 
                         input=uri_list.encode('utf-8'), check=True)
            
            # Set with xsel as backup
            subprocess.run(['xsel', '-b', '-i'], input=uri_list.encode('utf-8'), check=True)
            
            # Set GNOME format
            gnome_format = f"copy\n{uri_list}"
            subprocess.run(['xclip', '-selection', 'clipboard', 
                          '-t', 'x-special/gnome-copied-files', '-i'], 
                         input=gnome_format.encode('utf-8'), check=True)
            
            logging.info(f"Set clipboard with files: {', '.join(file_paths)}")
            
        except Exception as e:
            logging.error(f"Error setting file to clipboard: {e}")

    def get_clipboard_content(self):
        try:
            text_data = subprocess.run(['xsel', '-b', '-o'], 
                                     capture_output=True, text=True).stdout.strip()
            if text_data:
                return {'type': 'text', 'data': text_data}
            
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
                        with open(path, 'rb') as f:
                            chunks = []
                            while True:
                                chunk = f.read(MAX_CHUNK_SIZE)
                                if not chunk:
                                    break
                                chunks.append(base64.b64encode(chunk).decode('utf-8'))
                            
                        file_info = {
                            'name': os.path.basename(path),
                            'size': os.path.getsize(path),
                            'data': ''.join(chunks)
                        }
                        processed_files.append(file_info)
                    except Exception as e:
                        logging.error(f"Error processing file {path}: {e}")
                        continue
        
        if processed_files:
            return {'type': 'files', 'files': processed_files}
        return None

    def set_clipboard_content(self, content):
        try:
            self._clear_clipboard()
            
            if content['type'] == 'text':
                subprocess.run(['xsel', '-b', '-i'], 
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
                    logging.info(f"Files saved and added to clipboard")
                
        except Exception as e:
            logging.error(f"Error setting clipboard: {e}")
            self._clear_clipboard()

class AudioServer:
    # [Keep your existing AudioServer class implementation]
    pass

class UnifiedServer:
    def __init__(self):
        self.running = False
        self.clipboard_handler = ClipboardHandler()
        self.audio_server = AudioServer()
        logging.basicConfig(
            level=logging.INFO,
            format='%(asctime)s - %(levelname)s - %(message)s',
            datefmt='%Y-%m-%d %H:%M:%S'
        )

    def get_local_ip(self):
        """Get all local IP addresses"""
        ips = []
        try:
            interfaces = socket.getaddrinfo(
                host=socket.gethostname(),
                port=None,
                family=socket.AF_INET
            )
            ips = sorted(list(set(ip[4][0] for ip in interfaces if not ip[4][0].startswith('127.'))))
        except Exception as e:
            logging.error(f"Error getting local IPs: {e}")
            ips = ['0.0.0.0']
        return ips

    def handle_clipboard_client(self, client_socket, address):
        logging.info(f"New clipboard connection from {address}")
        self.clipboard_handler.clients.add(client_socket)
        
        try:
            while self.running:
                # Handle incoming data
                try:
                    client_socket.settimeout(0.1)
                    header = b""
                    while b":" not in header:
                        chunk = client_socket.recv(1)
                        if not chunk:
                            raise ConnectionError("Client disconnected")
                        header += chunk
                    
                    size = int(header.decode('utf-8').strip(":"))
                    data = b""
                    remaining = size
                    
                    while remaining > 0:
                        chunk = client_socket.recv(min(remaining, 8192))
                        if not chunk:
                            raise ConnectionError("Connection lost while receiving data")
                        data += chunk
                        remaining -= len(chunk)
                    
                    content = json.loads(data.decode('utf-8'))
                    
                    if content.get('type'):
                        self.clipboard_handler.set_clipboard_content(content)
                        
                        # Broadcast to other clients
                        message = f"{len(data)}:{data.decode('utf-8')}".encode('utf-8')
                        for other_client in self.clipboard_handler.clients:
                            if other_client != client_socket:
                                try:
                                    other_client.sendall(message)
                                except Exception as e:
                                    logging.error(f"Error broadcasting to client: {e}")
                                
                except socket.timeout:
                    pass
                except Exception as e:
                    logging.error(f"Error handling client data: {e}")
                    raise
                    
                time.sleep(0.1)
                
        except Exception as e:
            logging.error(f"Clipboard client error: {e}")
        finally:
            self.clipboard_handler.clients.remove(client_socket)
            client_socket.close()

    def start(self):
        self.running = True
        
        # Start clipboard server
        clipboard_server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        clipboard_server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        clipboard_server.bind(('0.0.0.0', CLIPBOARD_PORT))  # Bind to all interfaces
        clipboard_server.listen(5)
        
        # Start audio server
        audio_server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        audio_server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        audio_server.bind(('0.0.0.0', AUDIO_PORT))  # Bind to all interfaces
        audio_server.listen(5)
        
        local_ips = self.get_local_ip()
        logging.info("Server started on:")
        for ip in local_ips:
            logging.info(f"  {ip} - Clipboard port: {CLIPBOARD_PORT}, Audio port: {AUDIO_PORT}")
        
        def handle_clipboard_connections():
            while self.running:
                try:
                    client_socket, address = clipboard_server.accept()
                    logging.info(f"New clipboard connection from {address}")
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
                    logging.info(f"New audio connection from {address}")
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
            
        clipboard_server.close()
        audio_server.close()
        self.audio_server.cleanup_all()
        
        clipboard_thread.join()
        audio_thread.join()
        
        logging.info("Server shutdown complete")

if __name__ == "__main__":
    server = UnifiedServer()
    server.start()