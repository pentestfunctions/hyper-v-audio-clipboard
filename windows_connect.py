#!/usr/bin/env python3
import socket
import threading
import win32clipboard
import win32con
import pyaudio
import time
import base64
import os
import json
from pathlib import Path
import logging
import zlib
from queue import Queue

# Configuration
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
        self.transfer_queue = Queue()
        self.clipboard_dir = Path.home() / 'ClipboardSync'
        self.clipboard_dir.mkdir(exist_ok=True)
        
    def get_clipboard_content(self):
        try:
            win32clipboard.OpenClipboard()
            
            # Try text first
            if win32clipboard.IsClipboardFormatAvailable(win32con.CF_UNICODETEXT):
                data = win32clipboard.GetClipboardData(win32con.CF_UNICODETEXT)
                win32clipboard.CloseClipboard()
                return {'type': 'text', 'data': data}
            
            # Try files
            elif win32clipboard.IsClipboardFormatAvailable(win32con.CF_HDROP):
                files = win32clipboard.GetClipboardData(win32con.CF_HDROP)
                win32clipboard.CloseClipboard()
                return self._process_files(files)
                
            win32clipboard.CloseClipboard()
        except Exception as e:
            logging.error(f"Clipboard error: {e}")
            try:
                win32clipboard.CloseClipboard()
            except:
                pass
        return None

    def _process_files(self, files):
        processed_files = []
        for file_path in files:
            if os.path.exists(file_path):
                try:
                    with open(file_path, 'rb') as f:
                        # Use chunked reading for large files
                        chunks = []
                        while True:
                            chunk = f.read(MAX_CHUNK_SIZE)
                            if not chunk:
                                break
                            chunks.append(base64.b64encode(chunk).decode('utf-8'))
                    
                    file_info = {
                        'name': os.path.basename(file_path),
                        'size': os.path.getsize(file_path),
                        'data': ''.join(chunks)
                    }
                    processed_files.append(file_info)
                    logging.info(f"Processed file: {file_path}")
                except Exception as e:
                    logging.error(f"Error processing file {file_path}: {e}")
        
        if processed_files:
            return {'type': 'files', 'files': processed_files}
        return None

    def set_clipboard_content(self, content):
        try:
            if content['type'] == 'text':
                win32clipboard.OpenClipboard()
                win32clipboard.EmptyClipboard()
                win32clipboard.SetClipboardText(content['data'], win32con.CF_UNICODETEXT)
                win32clipboard.CloseClipboard()
                logging.info("Text content set to clipboard")
                
            elif content['type'] == 'files':
                # Clean up old files
                for file_path in self.clipboard_dir.glob('*'):
                    try:
                        if file_path.is_file():
                            file_path.unlink()
                    except Exception as e:
                        logging.error(f"Error deleting file {file_path}: {e}")
                
                saved_files = []
                for file_info in content['files']:
                    target_path = self.clipboard_dir / file_info['name']
                    with open(target_path, 'wb') as f:
                        f.write(base64.b64decode(file_info['data']))
                    saved_files.append(str(target_path))
                
                # Set files to clipboard
                file_paths = '\0'.join(saved_files + [''])
                win32clipboard.OpenClipboard()
                win32clipboard.EmptyClipboard()
                win32clipboard.SetClipboardData(win32con.CF_HDROP, file_paths)
                win32clipboard.CloseClipboard()
                
                logging.info(f"Files saved to clipboard: {', '.join(saved_files)}")
                
        except Exception as e:
            logging.error(f"Error setting clipboard: {e}")
            try:
                win32clipboard.CloseClipboard()
            except:
                pass

class AudioHandler:
    def __init__(self):
        self.p = pyaudio.PyAudio()
        self.streams = {'input': None, 'output': None}
        
    def setup_streams(self):
        self.streams['input'] = self.p.open(
            format=FORMAT,
            channels=CHANNELS,
            rate=RATE,
            input=True,
            frames_per_buffer=CHUNK
        )
        
        self.streams['output'] = self.p.open(
            format=FORMAT,
            channels=CHANNELS,
            rate=RATE,
            output=True,
            frames_per_buffer=CHUNK
        )

    def cleanup(self):
        for stream in self.streams.values():
            if stream:
                stream.stop_stream()
                stream.close()
        self.p.terminate()

class UnifiedClient:
    def __init__(self, host):
        self.host = host
        self.running = False
        self.clipboard = ClipboardHandler()
        self.audio = AudioHandler()
        logging.basicConfig(
            level=logging.INFO,
            format='%(asctime)s - %(levelname)s - %(message)s',
            datefmt='%Y-%m-%d %H:%M:%S'
        )
        
    def start_clipboard_sync(self):
            while self.running:
                try:
                    logging.info(f"Connecting to clipboard service {self.host}:{CLIPBOARD_PORT}...")
                    clipboard_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                    clipboard_socket.connect((self.host, CLIPBOARD_PORT))
                    logging.info("Clipboard sync connected!")

                    while self.running:
                        # Check local clipboard for changes
                        current = self.clipboard.get_clipboard_content()
                        if current and current != self.clipboard.last_content:
                            try:
                                data = json.dumps(current, ensure_ascii=False)
                                message = f"{len(data)}:{data}".encode('utf-8')
                                clipboard_socket.sendall(message)
                                self.clipboard.last_content = current
                            except Exception as e:
                                logging.error(f"Error sending clipboard content: {e}")

                        # Check for incoming data
                        try:
                            clipboard_socket.settimeout(0.1)
                            header = b""
                            while b":" not in header:
                                chunk = clipboard_socket.recv(1)
                                if not chunk:
                                    raise ConnectionError("Server disconnected")
                                header += chunk

                            size = int(header.decode('utf-8').strip(":"))
                            data = b""
                            remaining = size

                            while remaining > 0:
                                chunk = clipboard_socket.recv(min(remaining, 8192))
                                if not chunk:
                                    raise ConnectionError("Connection lost while receiving data")
                                data += chunk
                                remaining -= len(chunk)

                            content = json.loads(data.decode('utf-8'))

                            if content.get('type'):
                                self.clipboard.set_clipboard_content(content)
                                self.clipboard.last_content = content

                        except socket.timeout:
                            pass
                        except ConnectionError as e:
                            logging.error(f"Connection error: {e}")
                            raise
                        except Exception as e:
                            logging.error(f"Error receiving clipboard content: {e}")

                        time.sleep(0.1)

                except Exception as e:
                    logging.error(f"Clipboard connection error: {e}")
                    time.sleep(5)  # Wait before reconnecting

                finally:
                    try:
                        clipboard_socket.close()
                    except:
                        pass

    def start_audio_sync(self):
        while self.running:
            try:
                logging.info(f"Connecting to audio service {self.host}:{AUDIO_PORT}...")
                audio_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                audio_socket.connect((self.host, AUDIO_PORT))
                logging.info("Audio sync connected!")
                
                def receive_audio():
                    while self.running:
                        try:
                            data = audio_socket.recv(CHUNK * 4)
                            if data and self.audio.streams['output']:
                                self.audio.streams['output'].write(data)
                        except:
                            break
                            
                def send_audio():
                    while self.running:
                        try:
                            if self.audio.streams['input']:
                                data = self.audio.streams['input'].read(CHUNK, exception_on_overflow=False)
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
        self.audio.setup_streams()
        
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
        self.audio.cleanup()

if __name__ == "__main__":
    import sys
    
    if len(sys.argv) != 2:
        print("Usage: python windows_client.py <host_ip>")
        sys.exit(1)
        
    host_ip = sys.argv[1]
    client = UnifiedClient(host_ip)
    client.start()
