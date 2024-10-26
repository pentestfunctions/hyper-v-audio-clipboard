#!/usr/bin/env python3
import socket
import threading
import pyaudio
import time
import logging
from queue import Queue
import signal
import sys

# Audio Configuration (matching client settings)
CHUNK = 1024
FORMAT = pyaudio.paInt16
CHANNELS = 2
RATE = 44100
AUDIO_PORT = 5001

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

class AudioServer:
    def __init__(self, host='0.0.0.0'):
        self.host = host
        self.running = False
        self.clients = set()
        self.audio = AudioHandler()
        self.audio_queue = Queue()
        
        logging.basicConfig(
            level=logging.INFO,
            format='%(asctime)s - %(levelname)s - %(message)s',
            datefmt='%Y-%m-%d %H:%M:%S'
        )
        
        # Setup signal handler for graceful shutdown
        signal.signal(signal.SIGINT, self.signal_handler)
        signal.signal(signal.SIGTERM, self.signal_handler)

    def signal_handler(self, signum, frame):
        logging.info("Shutdown signal received. Cleaning up...")
        self.stop()

    def handle_client(self, client_socket, address):
        logging.info(f"New client connected from {address}")
        self.clients.add(client_socket)
        
        def receive_audio():
            while self.running:
                try:
                    data = client_socket.recv(CHUNK * 4)
                    if not data:
                        break
                    
                    # Play received audio locally
                    if self.audio.streams['output']:
                        self.audio.streams['output'].write(data)
                    
                    # Forward audio to other clients
                    for other_client in self.clients:
                        if other_client != client_socket:
                            try:
                                other_client.send(data)
                            except:
                                pass
                except Exception as e:
                    logging.error(f"Error receiving audio from {address}: {e}")
                    break
        
        def send_audio():
            while self.running:
                try:
                    if self.audio.streams['input']:
                        data = self.audio.streams['input'].read(CHUNK, exception_on_overflow=False)
                        client_socket.send(data)
                except Exception as e:
                    logging.error(f"Error sending audio to {address}: {e}")
                    break

        receive_thread = threading.Thread(target=receive_audio)
        send_thread = threading.Thread(target=send_audio)
        
        receive_thread.start()
        send_thread.start()
        
        receive_thread.join()
        send_thread.join()
        
        self.clients.remove(client_socket)
        client_socket.close()
        logging.info(f"Client {address} disconnected")

    def start(self):
        self.running = True
        self.audio.setup_streams()
        
        server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        
        try:
            server_socket.bind((self.host, AUDIO_PORT))
            server_socket.listen(5)
            logging.info(f"Audio server listening on {self.host}:{AUDIO_PORT}")
            
            while self.running:
                try:
                    client_socket, address = server_socket.accept()
                    client_thread = threading.Thread(
                        target=self.handle_client,
                        args=(client_socket, address)
                    )
                    client_thread.start()
                except Exception as e:
                    if self.running:
                        logging.error(f"Error accepting connection: {e}")
                    
        except Exception as e:
            logging.error(f"Server error: {e}")
            
        finally:
            self.stop()
            server_socket.close()

    def stop(self):
        self.running = False
        # Close all client connections
        for client in self.clients:
            try:
                client.close()
            except:
                pass
        self.clients.clear()
        self.audio.cleanup()

if __name__ == "__main__":
    server = AudioServer()
    server.start()
