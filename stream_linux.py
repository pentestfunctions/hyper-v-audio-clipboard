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

# Configuration
HOST = '192.168.0.207'
CHUNK = 1024
FORMAT = pyaudio.paInt16
CHANNELS = 2
RATE = 44100
CLIPBOARD_PORT = 5000
AUDIO_PORT = 5001
MAX_CHUNK_SIZE = 1024 * 1024  # 1MB chunks for large files

class ClipboardHandler:
    def __init__(self):
        self.last_content = None
        
    def _clear_clipboard(self):
        try:
            # Clear using xsel
            subprocess.run(['xsel', '-b', '-c'], check=True)
            # Clear using xclip as backup
            subprocess.run(['xclip', '-selection', 'clipboard', '-i'], input=b'', check=True)
        except Exception as e:
            logging.error(f"Error clearing clipboard: {e}")

    def _set_file_to_clipboard(self, file_paths):
        """
        Set file URIs to clipboard using both GNOME/GTK and KDE methods
        """
        try:
            # Format paths as URIs
            uri_list = '\n'.join([f"file://{path}" for path in file_paths])
            
            # Set with xclip for GNOME/GTK
            subprocess.run(['xclip', '-selection', 'clipboard', '-t', 'text/uri-list', '-i'], 
                         input=uri_list.encode(), check=True)
            
            # Also set with regular clipboard for KDE
            subprocess.run(['xclip', '-selection', 'clipboard', '-i'], 
                         input=uri_list.encode(), check=True)
            
            # Backup with xsel
            subprocess.run(['xsel', '-b', '-i'], input=uri_list.encode(), check=True)
            
            # Set x-special/gnome-copied-files format for better GNOME compatibility
            gnome_format = f"copy\n{uri_list}"
            subprocess.run(['xclip', '-selection', 'clipboard', 
                          '-t', 'x-special/gnome-copied-files', '-i'], 
                         input=gnome_format.encode(), check=True)
            
        except Exception as e:
            logging.error(f"Error setting file to clipboard: {e}")

    def get_clipboard_content(self):
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
                
            elif content['type'] == 'files':
                save_dir = Path.home() / 'ClipboardSync'
                save_dir.mkdir(exist_ok=True)
                
                saved_paths = []
                for file_info in content['files']:
                    target_path = save_dir / file_info['name']
                    with open(target_path, 'wb') as f:
                        f.write(base64.b64decode(file_info['data']))
                    saved_paths.append(str(target_path.absolute()))
                
                # Set the saved files to clipboard
                self._set_file_to_clipboard(saved_paths)
                logging.info(f"Files saved to: {save_dir} and added to clipboard")
                
        except Exception as e:
            logging.error(f"Error setting clipboard: {e}")
            # Try to clear clipboard on error
            self._clear_clipboard()

def _receive_file(self, conn, file_info):
    try:
        save_dir = Path.home() / 'ClipboardSync'
        save_dir.mkdir(exist_ok=True)
        file_path = save_dir / file_info['name']
        
        received_size = 0
        total_size = file_info['size']
        saved_paths = []
        
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
                progress = (received_size / total_size) * 100
                logging.info(f"Receiving {file_info['name']}: {progress:.1f}%")
        
        saved_paths.append(str(file_path.absolute()))
        logging.info(f"File saved: {file_path}")
        
        # Set the received files to clipboard
        self._set_file_to_clipboard(saved_paths)
        
    except Exception as e:
        logging.error(f"Error receiving file {file_info['name']}: {e}")
class UnifiedClient:
    def __init__(self):
        self.running = False
        self.clipboard = ClipboardHandler()
        self.p = pyaudio.PyAudio()
        self.audio_streams = {'input': None, 'output': None}
        logging.basicConfig(level=logging.INFO)

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
                
                while sent_size < total_size:
                    chunk = f.read(MAX_CHUNK_SIZE)
                    if not chunk:
                        break
                    
                    # Compress chunk
                    compressed = zlib.compress(chunk)
                    size_header = f"{len(compressed)}:".encode()
                    conn.send(size_header + compressed)
                    
                    sent_size += len(chunk)
                    progress = (sent_size / total_size) * 100
                    logging.info(f"Sending {file_info['name']}: {progress:.1f}%")
                    
        except Exception as e:
            logging.error(f"Error sending file {file_info['name']}: {e}")

    def _receive_file(self, conn, file_info):
        try:
            save_dir = Path.home() / 'ClipboardSync'
            save_dir.mkdir(exist_ok=True)
            file_path = save_dir / file_info['name']
            
            received_size = 0
            total_size = file_info['size']
            
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
                    progress = (received_size / total_size) * 100
                    logging.info(f"Receiving {file_info['name']}: {progress:.1f}%")
                    
            logging.info(f"File saved: {file_path}")
            
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
                    current = self.clipboard.get_clipboard_content()
                    if current and current != self.clipboard.last_content:
                        if current['type'] == 'files':
                            for file_info in current['files']:
                                self.handle_file_transfer(clipboard_socket, file_info)
                        else:
                            data = json.dumps(current)
                            clipboard_socket.send(f"{len(data)}:".encode() + data.encode())
                        self.clipboard.last_content = current
                    
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
                            self.clipboard.set_clipboard_content(content)
                            self.clipboard.last_content = content
                            
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
