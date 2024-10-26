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
import dbus
from gi.repository import GLib
from dbus.mainloop.glib import DBusGMainLoop

# Configuration
CHUNK = 1024
FORMAT = pyaudio.paInt16
CHANNELS = 2
RATE = 44100
HOST = '0.0.0.0'
CLIPBOARD_PORT = 5000
AUDIO_PORT = 5001
MAX_CHUNK_SIZE = 1024 * 1024
RECONNECT_DELAY = 5  # seconds to wait before reconnecting services

class SessionManager:
    def __init__(self, on_unlock_callback=None):
        self.on_unlock_callback = on_unlock_callback
        self.screensaver_active = False
        self.setup_dbus_listener()

    def setup_dbus_listener(self):
        try:
            DBusGMainLoop(set_as_default=True)
            self.bus = dbus.SessionBus()
            
            # Listen for screensaver signals
            self.screensaver = self.bus.get_object(
                'org.gnome.ScreenSaver',
                '/org/gnome/ScreenSaver'
            )
            self.screensaver.connect_to_signal(
                'ActiveChanged',
                self.handle_screensaver_change
            )
            
            # Start GLib main loop in a separate thread
            self.loop = GLib.MainLoop()
            self.loop_thread = threading.Thread(target=self.loop.run)
            self.loop_thread.daemon = True
            self.loop_thread.start()
            
            logging.info("Session manager initialized successfully")
        except Exception as e:
            logging.error(f"Failed to initialize session manager: {e}")

    def handle_screensaver_change(self, active):
        self.screensaver_active = active
        if not active and self.on_unlock_callback:
            logging.info("Screen unlocked - triggering services recovery")
            self.on_unlock_callback()

class ClipboardHandler:
    def __init__(self):
        self.last_content = None
        self.clipboard_dir = Path.home() / 'ClipboardSync'
        self.clipboard_dir.mkdir(exist_ok=True)
        self.clients = set()
        self.clipboard_lock = threading.Lock()
        
    def reconnect_clipboard(self):
        """Attempt to reconnect to the X server clipboard"""
        try:
            with self.clipboard_lock:
                # Force reconnection to X clipboard
                subprocess.run(['xsel', '-b', '-c'], check=True)
                logging.info("Successfully reconnected to X clipboard")
                return True
        except Exception as e:
            logging.error(f"Failed to reconnect to clipboard: {e}")
            return False

    def _clear_clipboard(self):
        with self.clipboard_lock:
            try:
                subprocess.run(['xsel', '-b', '-c'], check=True)
                subprocess.run(['xclip', '-selection', 'clipboard', '-i'], input=b'', check=True)
                return True
            except Exception as e:
                logging.error(f"Error clearing clipboard: {e}")
                return False

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
            subprocess.run(
                ['xclip', '-selection', 'clipboard', '-t', 'text/uri-list', '-i'],
                input=uri_list.encode('utf-8'),
                check=True,
                timeout=2
            )
            
            # Set with xsel as backup
            subprocess.run(
                ['xsel', '-b', '-i'],
                input=uri_list.encode('utf-8'),
                check=True,
                timeout=2
            )
            
            # Set GNOME format
            gnome_format = f"copy\n{uri_list}"
            subprocess.run(
                ['xclip', '-selection', 'clipboard', '-t', 'x-special/gnome-copied-files', '-i'],
                input=gnome_format.encode('utf-8'),
                check=True,
                timeout=2
            )
            
            logging.info(f"Set clipboard with files: {', '.join(file_paths)}")
            
        except Exception as e:
            logging.error(f"Error setting file to clipboard: {e}")

    def get_clipboard_content(self):
        with self.clipboard_lock:
            try:
                # Try multiple times with delay
                for _ in range(3):
                    try:
                        text_data = subprocess.run(
                            ['xsel', '-b', '-o'],
                            capture_output=True,
                            text=True,
                            timeout=2
                        ).stdout.strip()
                        
                        if text_data:
                            return {'type': 'text', 'data': text_data}
                        
                        file_data = subprocess.run(
                            ['xclip', '-selection', 'clipboard', '-t', 'text/uri-list', '-o'],
                            capture_output=True,
                            text=True,
                            timeout=2
                        ).stdout
                        
                        if file_data:
                            return self._process_files(file_data)
                            
                        return None
                        
                    except subprocess.TimeoutExpired:
                        logging.warning("Clipboard access timed out, retrying...")
                        time.sleep(0.5)
                        continue
                        
            except Exception as e:
                logging.error(f"Clipboard error: {e}")
                self.reconnect_clipboard()
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
        with self.clipboard_lock:
            try:
                if not self._clear_clipboard():
                    if not self.reconnect_clipboard() or not self._clear_clipboard():
                        raise Exception("Failed to clear clipboard")
                
                if content['type'] == 'text':
                    subprocess.run(
                        ['xsel', '-b', '-i'],
                        input=content['data'].encode('utf-8'),
                        check=True,
                        timeout=2
                    )
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
                        
            except Exception as e:
                logging.error(f"Error setting clipboard: {e}")
                self.reconnect_clipboard()
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
                try:
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
                    logging.info(f"Audio streams setup for client {client_id}")
                except Exception as e:
                    logging.error(f"Error setting up audio streams: {e}")
                    raise

    def cleanup_stream(self, client_id):
        with self.audio_lock:
            if client_id in self.streams:
                for stream in self.streams[client_id].values():
                    if stream:
                        try:
                            stream.stop_stream()
                            stream.close()
                        except Exception as e:
                            logging.error(f"Error cleaning up stream: {e}")
                del self.streams[client_id]
                logging.info(f"Cleaned up streams for client {client_id}")

    def cleanup_all(self):
        with self.audio_lock:
            for client_id in list(self.streams.keys()):
                self.cleanup_stream(client_id)
            try:
                self.p.terminate()
            except Exception as e:
                logging.error(f"Error terminating PyAudio: {e}")
        logging.info("All audio resources cleaned up")

    def handle_client(self, client_socket, client_address):
        client_id = f"{client_address[0]}:{client_address[1]}"
        self.clients.add(client_socket)
        client_running = True
        logging.info(f"New audio client connected: {client_id}")
        
        try:
            self.setup_stream(client_id)
            
            def receive_audio():
                while self.running and client_running and client_socket in self.clients:
                    try:
                        data = client_socket.recv(CHUNK * 4)
                        if not data:
                            break
                        
                        # Broadcast to other clients
                        for other_client in self.clients:
                            if other_client != client_socket:
                                try:
                                    other_client.send(data)
                                except Exception as e:
                                    logging.error(f"Error sending audio to client: {e}")
                                    
                        # Play locally if stream exists
                        if client_id in self.streams:
                            try:
                                self.streams[client_id]['output'].write(data)
                            except Exception as e:
                                logging.error(f"Error playing audio: {e}")
                    except Exception as e:
                        logging.error(f"Error receiving audio: {e}")
                        break

            def send_audio():
                while self.running and client_running and client_socket in self.clients:
                    try:
                        if client_id in self.streams:
                            data = self.streams[client_id]['input'].read(CHUNK, exception_on_overflow=False)
                            client_socket.send(data)
                    except Exception as e:
                        logging.error(f"Error sending audio: {e}")
                        break

            receive_thread = threading.Thread(target=receive_audio)
            send_thread = threading.Thread(target=send_audio)
            
            receive_thread.daemon = True
            send_thread.daemon = True
            
            receive_thread.start()
            send_thread.start()
            
            # Wait for threads to complete
            while self.running and client_running and client_socket in self.clients:
                time.sleep(0.1)
            
        except Exception as e:
            logging.error(f"Audio client error: {e}")
        finally:
            client_running = False
            self.clients.remove(client_socket)
            self.cleanup_stream(client_id)
            try:
                client_socket.close()
            except:
                pass
            logging.info(f"Audio client disconnected: {client_id}")

class UnifiedServer:
    def __init__(self):
        self.running = False
        self.clipboard_handler = ClipboardHandler()
        self.audio_server = AudioServer()
        self.setup_logging()
        
        # Initialize session manager with recovery callback
        self.session_manager = SessionManager(
            on_unlock_callback=self.recover_services
        )

    def recover_services(self):
        """Attempt to recover all services after screen unlock"""
        logging.info("Attempting to recover services...")
        
        # Recover clipboard
        if not self.clipboard_handler.reconnect_clipboard():
            logging.error("Failed to recover clipboard service")
            
        # Recover audio
        try:
            self.audio_server.cleanup_all()
            time.sleep(1)  # Give time for cleanup
            self.audio_server = AudioServer()  # Reinitialize audio
            logging.info("Audio service recovered successfully")
        except Exception as e:
            logging.error(f"Failed to recover audio service: {e}")

    def setup_logging(self):
        log_dir = Path.home() / '.clipboard_sync_logs'
        log_dir.mkdir(exist_ok=True)
        
        logging.basicConfig(
            level=logging.INFO,
            format='%(asctime)s - %(levelname)s - %(message)s',
            datefmt='%Y-%m-%d %H:%M:%S',
            handlers=[
                logging.StreamHandler(),
                logging.FileHandler(log_dir / 'sync_server.log')
            ]
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
            try:
                client_socket.close()
            except:
                pass

    def start(self):
        self.running = True
        self.audio_server.running = True  # Set audio server running flag
        
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
                    clipboard_server.settimeout(1)  # Add timeout to allow checking running flag
                    try:
                        client_socket, address = clipboard_server.accept()
                        thread = threading.Thread(
                            target=self.handle_clipboard_client,
                            args=(client_socket, address)
                        )
                        thread.daemon = True
                        thread.start()
                    except socket.timeout:
                        continue
                except Exception as e:
                    if self.running:
                        logging.error(f"Clipboard server error: {e}")
                        time.sleep(RECONNECT_DELAY)

        def handle_audio_connections():
            while self.running:
                try:
                    audio_server.settimeout(1)  # Add timeout to allow checking running flag
                    try:
                        client_socket, address = audio_server.accept()
                        thread = threading.Thread(
                            target=self.audio_server.handle_client,
                            args=(client_socket, address)
                        )
                        thread.daemon = True
                        thread.start()
                    except socket.timeout:
                        continue
                except Exception as e:
                    if self.running:
                        logging.error(f"Audio server error: {e}")
                        time.sleep(RECONNECT_DELAY)

        clipboard_thread = threading.Thread(target=handle_clipboard_connections)
        audio_thread = threading.Thread(target=handle_audio_connections)
        
        clipboard_thread.daemon = True
        audio_thread.daemon = True
        
        clipboard_thread.start()
        audio_thread.start()
        
        try:
            while self.running:
                time.sleep(0.1)
        except KeyboardInterrupt:
            logging.info("Shutting down server...")
            self.running = False
            
        # Cleanup
        self.running = False
        self.audio_server.running = False
        
        # Close all client connections
        for client in self.clipboard_handler.clients.copy():
            try:
                client.close()
            except:
                pass
                
        for client in self.audio_server.clients.copy():
            try:
                client.close()
            except:
                pass
        
        # Close server sockets
        try:
            clipboard_server.close()
        except:
            pass
            
        try:
            audio_server.close()
        except:
            pass
            
        self.audio_server.cleanup_all()
        
        # Wait for threads to complete
        clipboard_thread.join(timeout=5)
        audio_thread.join(timeout=5)
        
        logging.info("Server shutdown complete")

if __name__ == "__main__":
    server = UnifiedServer()
    server.start()
