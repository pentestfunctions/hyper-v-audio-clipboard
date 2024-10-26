#!/usr/bin/env python3
import socket
import threading
import pyaudio
import time
import base64
import os
import json
import logging
import zlib
import struct
from pathlib import Path
from io import BytesIO
from concurrent.futures import ThreadPoolExecutor
import gi
gi.require_version('Gtk', '3.0')
from gi.repository import Gtk, Gdk, GLib

# Constants matching the Windows client
CHUNK = 4096
FORMAT = pyaudio.paFloat32
CHANNELS = 2
RATE = 48000
CLIPBOARD_PORT = 5000
AUDIO_PORT = 5001
MAX_PACKET_SIZE = 8192
COMPRESSION_LEVEL = 6

class LinuxClipboardHandler:
    def __init__(self):
        self.clipboard = Gtk.Clipboard.get(Gdk.SELECTION_CLIPBOARD)
        self.last_content = None
        self.clipboard_dir = Path.home() / 'ClipboardSync'
        self.clipboard_dir.mkdir(exist_ok=True)
        # Connect clipboard owner-change signal
        self.clipboard.connect('owner-change', self.on_clipboard_change)

    def on_clipboard_change(self, clipboard, event):
        content = self.get_clipboard_content()
        if content:
            self.last_content = content
            # Notify all connected clients of the change
            if hasattr(self, 'notify_callback'):
                self.notify_callback(content)

    def _compress_data(self, data):
        return base64.b64encode(zlib.compress(data, COMPRESSION_LEVEL)).decode('utf-8')
    
    def _decompress_data(self, data):
        return zlib.decompress(base64.b64decode(data))

    def get_clipboard_content(self):
        try:
            # Check for text
            text = self.clipboard.wait_for_text()
            if text is not None:
                return {'type': 'text', 'data': text}

            # Check for files (URIs)
            uris = self.clipboard.wait_for_uris()
            if uris:
                return self._process_files(uris)

            # Check for image
            pixbuf = self.clipboard.wait_for_image()
            if pixbuf is not None:
                return self._process_image(pixbuf)

        except Exception as e:
            logging.error(f"Clipboard error: {e}")
        return None

    def _process_files(self, uris):
        processed = []
        with ThreadPoolExecutor(max_workers=4) as executor:
            futures = []
            for uri in uris:
                if uri.startswith('file://'):
                    path = uri[7:]  # Remove 'file://' prefix
                    if os.path.exists(path):
                        futures.append(executor.submit(self._process_single_file, path))
            
            for future in futures:
                result = future.result()
                if result:
                    processed.append(result)
                    
        return {'type': 'files', 'files': processed} if processed else None

    def _process_single_file(self, path):
        try:
            chunk_size = 1024 * 1024  # 1MB chunks
            chunks = []
            file_size = os.path.getsize(path)
            
            with open(path, 'rb') as f:
                while True:
                    chunk = f.read(chunk_size)
                    if not chunk:
                        break
                    chunks.append(self._compress_data(chunk))
            
            return {
                'name': os.path.basename(path),
                'size': file_size,
                'chunks': chunks,
                'mime_type': self._get_mime_type(path)
            }
        except Exception as e:
            logging.error(f"File processing error: {e}")
            return None

    def _get_mime_type(self, path):
        import mimetypes
        mime_type, _ = mimetypes.guess_type(path)
        return mime_type or 'application/octet-stream'

    def _process_image(self, pixbuf):
        try:
            success, buffer = pixbuf.save_to_bufferv('png', [], [])
            if success:
                compressed_data = self._compress_data(buffer)
                return {
                    'type': 'image',
                    'format': 'png',
                    'data': compressed_data
                }
        except Exception as e:
            logging.error(f"Image processing error: {e}")
        return None

    def set_clipboard_content(self, content):
        try:
            if content['type'] == 'text':
                GLib.idle_add(self._set_text_clipboard, content['data'])
            elif content['type'] == 'files':
                GLib.idle_add(self._set_files_clipboard, content['files'])
            elif content['type'] == 'image':
                GLib.idle_add(self._set_image_clipboard, content)
        except Exception as e:
            logging.error(f"Set clipboard error: {e}")

    def _set_text_clipboard(self, text):
        self.clipboard.set_text(text, -1)
        self.clipboard.store()

    def _set_files_clipboard(self, files):
        # Clean up old files
        for f in self.clipboard_dir.glob('*'):
            try:
                f.unlink()
            except:
                pass
        
        uris = []
        for file_info in files:
            try:
                path = self.clipboard_dir / file_info['name']
                with open(path, 'wb') as f:
                    for chunk in file_info['chunks']:
                        f.write(self._decompress_data(chunk))
                uris.append(f'file://{path}')
            except Exception as e:
                logging.error(f"Error saving file {file_info['name']}: {e}")
        
        if uris:
            self.clipboard.set_uris(uris)
            self.clipboard.store()

class AudioServer:
    def __init__(self):
        self.p = pyaudio.PyAudio()
        self.streams = {'input': None, 'output': None}
        self.audio_buffer = BytesIO()
        self.buffer_size = CHUNK * 10
        self.clients = set()
        self.lock = threading.Lock()

    def setup_streams(self):
        self.streams.update({
            'input': self.p.open(
                format=FORMAT,
                channels=CHANNELS,
                rate=RATE,
                input=True,
                frames_per_buffer=CHUNK,
                stream_callback=self._input_callback
            ),
            'output': self.p.open(
                format=FORMAT,
                channels=CHANNELS,
                rate=RATE,
                output=True,
                frames_per_buffer=CHUNK,
                stream_callback=self._output_callback
            )
        })
        
        for stream in self.streams.values():
            stream.start_stream()

    def _input_callback(self, in_data, frame_count, time_info, status):
        # Broadcast audio to all connected clients
        with self.lock:
            for client in self.clients.copy():
                try:
                    client.sendall(struct.pack('!I', len(in_data)) + in_data)
                except:
                    self.clients.remove(client)
        return (in_data, pyaudio.paContinue)

    def _output_callback(self, in_data, frame_count, time_info, status):
        data = self.audio_buffer.read(frame_count * 4)
        if len(data) < frame_count * 4:
            data = data + b'\0' * (frame_count * 4 - len(data))
        return (data, pyaudio.paContinue)

    def add_client(self, client_socket):
        with self.lock:
            self.clients.add(client_socket)

    def remove_client(self, client_socket):
        with self.lock:
            self.clients.discard(client_socket)

    def cleanup(self):
        for stream in self.streams.values():
            if stream:
                stream.stop_stream()
                stream.close()
        self.p.terminate()

class SyncServer:
    def __init__(self, bind_addr='0.0.0.0'):
        self.bind_addr = bind_addr
        self.running = False
        self.clipboard = LinuxClipboardHandler()
        self.audio = AudioServer()
        self.clipboard_clients = set()
        self.clipboard_lock = threading.Lock()
        
        logging.basicConfig(
            level=logging.INFO,
            format='%(asctime)s - %(levelname)s - %(message)s',
            handlers=[
                logging.FileHandler('sync_server.log'),
                logging.StreamHandler()
            ]
        )

        # Set up clipboard change notification
        self.clipboard.notify_callback = self.broadcast_clipboard

    def _send_data(self, sock, data):
        try:
            serialized = json.dumps(data).encode('utf-8')
            compressed = zlib.compress(serialized, COMPRESSION_LEVEL)
            size = len(compressed)
            sock.sendall(struct.pack('!I', size) + compressed)
        except Exception as e:
            raise ConnectionError(f"Send error: {e}")

    def _receive_data(self, sock):
        try:
            size_data = sock.recv(4)
            if not size_data:
                raise ConnectionError("Connection closed")
            
            size = struct.unpack('!I', size_data)[0]
            data = b''
            while len(data) < size:
                chunk = sock.recv(min(size - len(data), MAX_PACKET_SIZE))
                if not chunk:
                    raise ConnectionError("Connection lost")
                data += chunk
            
            decompressed = zlib.decompress(data)
            return json.loads(decompressed)
        except Exception as e:
            raise ConnectionError(f"Receive error: {e}")

    def broadcast_clipboard(self, content):
        with self.clipboard_lock:
            disconnected = set()
            for client in self.clipboard_clients:
                try:
                    self._send_data(client, content)
                except:
                    disconnected.add(client)
            
            # Remove disconnected clients
            self.clipboard_clients.difference_update(disconnected)

    def handle_clipboard_client(self, client_socket):
        with self.clipboard_lock:
            self.clipboard_clients.add(client_socket)
        
        try:
            while self.running:
                try:
                    content = self._receive_data(client_socket)
                    if content and content.get('type'):
                        # Update local clipboard
                        self.clipboard.set_clipboard_content(content)
                        # Broadcast to other clients
                        with self.clipboard_lock:
                            for other_client in self.clipboard_clients:
                                if other_client != client_socket:
                                    try:
                                        self._send_data(other_client, content)
                                    except:
                                        self.clipboard_clients.discard(other_client)
                except socket.timeout:
                    continue
                except Exception as e:
                    logging.error(f"Clipboard client error: {e}")
                    break
        finally:
            with self.clipboard_lock:
                self.clipboard_clients.discard(client_socket)
            client_socket.close()

    def handle_audio_client(self, client_socket):
        self.audio.add_client(client_socket)
        try:
            while self.running:
                try:
                    size_data = client_socket.recv(4)
                    if not size_data:
                        break
                    
                    size = struct.unpack('!I', size_data)[0]
                    data = client_socket.recv(size)
                    if data:
                        self.audio.audio_buffer.write(data)
                        if self.audio.audio_buffer.tell() > self.audio.buffer_size:
                            self.audio.audio_buffer.seek(0)
                except Exception as e:
                    logging.error(f"Audio client error: {e}")
                    break
        finally:
            self.audio.remove_client(client_socket)
            client_socket.close()

    def start(self):
        self.running = True
        self.audio.setup_streams()

        # Start clipboard server
        clipboard_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        clipboard_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        clipboard_socket.bind((self.bind_addr, CLIPBOARD_PORT))
        clipboard_socket.listen(5)

        # Start audio server
        audio_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        audio_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        audio_socket.bind((self.bind_addr, AUDIO_PORT))
        audio_socket.listen(5)

        def accept_clipboard_clients():
            while self.running:
                try:
                    client, addr = clipboard_socket.accept()
                    logging.info(f"New clipboard client from {addr}")
                    thread = threading.Thread(
                        target=self.handle_clipboard_client,
                        args=(client,)
                    )
                    thread.daemon = True
                    thread.start()
                except Exception as e:
                    logging.error(f"Clipboard accept error: {e}")

        def accept_audio_clients():
            while self.running:
                try:
                    client, addr = audio_socket.accept()
                    logging.info(f"New audio client from {addr}")
                    thread = threading.Thread(
                        target=self.handle_audio_client,
                        args=(client,)
                    )
                    thread.daemon = True
                    thread.start()
                except Exception as e:
                    logging.error(f"Audio accept error: {e}")

        # Start accept threads
        threads = [
            threading.Thread(target=accept_clipboard_clients),
            threading.Thread(target=accept_audio_clients)
        ]
        for thread in threads:
            thread.daemon = True
            thread.start()

        try:
            # Start GTK main loop for clipboard handling
            Gtk.main()
        except KeyboardInterrupt:
            self.running = False
        finally:
            clipboard_socket.close()
            audio_socket.close()
            self.audio.cleanup()

if __name__ == "__main__":
    server = SyncServer()
    server.start()
