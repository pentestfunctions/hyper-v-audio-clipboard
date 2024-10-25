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
CLIPBOARD_CHECK_INTERVAL = 0.5  # How often to check clipboard for changes

class AudioServer:
    def __init__(self, host='0.0.0.0', port=AUDIO_PORT):
        self.host = host
        self.port = port
        self.p = pyaudio.PyAudio()
        self.audio_stream = self.p.open(format=FORMAT, channels=CHANNELS, rate=RATE,
                                      input=True, frames_per_buffer=CHUNK)
        self.clients = []
        self.running = False
        self.accept_thread = None
        self.stream_thread = None

    def start(self):
        self.running = True
        # Set up server socket
        self.server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.server_socket.bind((self.host, self.port))
        self.server_socket.listen(5)
        logging.info(f"Audio server listening on {self.host}:{self.port}")

        # Start accept thread
        self.accept_thread = threading.Thread(target=self.accept_clients)
        self.accept_thread.daemon = True
        self.accept_thread.start()

        # Start streaming thread
        self.stream_thread = threading.Thread(target=self.stream_audio)
        self.stream_thread.daemon = True
        self.stream_thread.start()

    def stop(self):
        self.running = False
        # Close all client connections
        for client in self.clients:
            try:
                client.close()
            except:
                pass
        self.clients.clear()
        
        # Close server socket
        try:
            self.server_socket.close()
        except:
            pass
            
        # Close audio stream
        try:
            self.audio_stream.stop_stream()
            self.audio_stream.close()
            self.p.terminate()
        except:
            pass

    def accept_clients(self):
        while self.running:
            try:
                self.server_socket.settimeout(1.0)  # Allow checking running flag
                client_socket, client_address = self.server_socket.accept()
                logging.info(f"Audio client {client_address} connected")
                self.clients.append(client_socket)
            except socket.timeout:
                continue
            except Exception as e:
                if self.running:
                    logging.error(f"Error accepting audio client: {e}")
                break

    def stream_audio(self):
        while self.running:
            try:
                # Capture audio
                audio_data = self.audio_stream.read(CHUNK, exception_on_overflow=False)
                # Send to each connected client
                for client in self.clients[:]:  # Create a copy of the list for iteration
                    try:
                        client.sendall(audio_data)
                    except Exception as e:
                        logging.error(f"Error sending audio to client: {e}")
                        self.clients.remove(client)
                        try:
                            client.close()
                        except:
                            pass
            except Exception as e:
                if self.running:
                    logging.error(f"Audio streaming error: {e}")

[Previous ClipboardHandler class code remains exactly the same]

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
        
        # Start clipboard monitoring
        self.clipboard_handler.start_monitoring()
        
        # Start audio server
        self.audio_server.start()
        
        # Start clipboard server
        clipboard_server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        clipboard_server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        clipboard_server.bind(('0.0.0.0', CLIPBOARD_PORT))
        clipboard_server.listen(5)
        
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
        
        clipboard_thread = threading.Thread(target=handle_clipboard_connections)
        clipboard_thread.start()
        
        try:
            while self.running:
                time.sleep(0.1)
        except KeyboardInterrupt:
            logging.info("Shutting down server...")
            self.running = False
            
        # Stop clipboard monitoring
        self.clipboard_handler.stop_monitoring()
        
        # Stop audio server
        self.audio_server.stop()
        
        clipboard_server.close()
        clipboard_thread.join()
        
        logging.info("Server shutdown complete")

if __name__ == "__main__":
    server = UnifiedServer()
    server.start()
