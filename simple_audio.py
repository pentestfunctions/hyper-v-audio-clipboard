#!/usr/bin/env python3
import socket
import pyaudio
import threading
import logging

# Audio Configuration
CHUNK = 1024
FORMAT = pyaudio.paInt16
CHANNELS = 2
RATE = 44100
AUDIO_PORT = 5001

class AudioServer:
    def __init__(self, host='0.0.0.0', port=AUDIO_PORT):
        self.host = host
        self.port = port
        self.p = pyaudio.PyAudio()
        self.audio_stream = self.p.open(format=FORMAT, channels=CHANNELS, rate=RATE,
                                        input=True, frames_per_buffer=CHUNK)
        self.clients = []
        self.running = True

    def start_server(self):
        # Set up server socket
        server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        server_socket.bind((self.host, self.port))
        server_socket.listen(5)
        logging.info(f"Server listening on {self.host}:{self.port}")

        # Accept new clients
        accept_thread = threading.Thread(target=self.accept_clients, args=(server_socket,))
        accept_thread.start()

        # Stream audio to clients
        self.stream_audio()

        # Clean up
        self.running = False
        self.audio_stream.stop_stream()
        self.audio_stream.close()
        self.p.terminate()
        server_socket.close()

    def accept_clients(self, server_socket):
        while self.running:
            client_socket, client_address = server_socket.accept()
            logging.info(f"Client {client_address} connected")
            self.clients.append(client_socket)

    def stream_audio(self):
        while self.running:
            try:
                # Capture audio
                audio_data = self.audio_stream.read(CHUNK, exception_on_overflow=False)
                # Send to each connected client
                for client in self.clients:
                    try:
                        client.sendall(audio_data)
                    except Exception as e:
                        logging.error(f"Error sending audio to client: {e}")
                        self.clients.remove(client)
            except Exception as e:
                logging.error(f"Audio streaming error: {e}")
                break

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
    server = AudioServer()
    server.start_server()
