#!/usr/bin/env python3
import socket
import threading
import pyaudio
import time
import logging
from queue import Queue
import signal
import sys

# Audio Configuration - adjusted for PipeWire/XRDP
CHUNK = 1024
FORMAT = pyaudio.paFloat32  # Changed to float32 to match PipeWire's default
CHANNELS = 2
RATE = 48000  # Changed to match your PipeWire configuration
AUDIO_PORT = 5001

class AudioHandler:
    def __init__(self):
        self.p = pyaudio.PyAudio()
        self.streams = {'input': None, 'output': None}
        self.input_device_index = None
        self.output_device_index = None
        
    def find_xrdp_devices(self):
        """Find XRDP source and sink devices"""
        for i in range(self.p.get_device_count()):
            try:
                device_info = self.p.get_device_info_by_index(i)
                device_name = device_info['name'].lower()
                
                # Look for XRDP devices
                if 'xrdp' in device_name and 'source' in device_name:
                    self.input_device_index = i
                    logging.info(f"Found XRDP source device: {device_info['name']}")
                elif 'xrdp' in device_name and 'sink' in device_name:
                    self.output_device_index = i
                    logging.info(f"Found XRDP sink device: {device_info['name']}")
                
            except Exception as e:
                logging.error(f"Error checking device {i}: {e}")

    def list_devices(self):
        """List all available audio devices"""
        info = []
        for i in range(self.p.get_device_count()):
            try:
                device_info = self.p.get_device_info_by_index(i)
                info.append(f"Device {i}: {device_info['name']}")
                info.append(f"  Input channels: {device_info['maxInputChannels']}")
                info.append(f"  Output channels: {device_info['maxOutputChannels']}")
                info.append(f"  Default SampleRate: {device_info['defaultSampleRate']}")
                info.append("")
            except Exception as e:
                info.append(f"Error getting device {i} info: {e}")
        return '\n'.join(info)

    def setup_streams(self):
        """Setup audio streams specifically for XRDP environment"""
        logging.info("Available audio devices:\n" + self.list_devices())
        
        # Find XRDP devices
        self.find_xrdp_devices()
        
        # Setup input stream
        try:
            if self.input_device_index is not None:
                self.streams['input'] = self.p.open(
                    format=FORMAT,
                    channels=CHANNELS,
                    rate=RATE,
                    input=True,
                    input_device_index=self.input_device_index,
                    frames_per_buffer=CHUNK,
                    stream_callback=None
                )
                logging.info("Input stream setup successfully")
            else:
                logging.error("No XRDP source device found")
        except Exception as e:
            logging.error(f"Error setting up input stream: {e}")

        # Setup output stream
        try:
            if self.output_device_index is not None:
                self.streams['output'] = self.p.open(
                    format=FORMAT,
                    channels=CHANNELS,
                    rate=RATE,
                    output=True,
                    output_device_index=self.output_device_index,
                    frames_per_buffer=CHUNK,
                    stream_callback=None
                )
                logging.info("Output stream setup successfully")
            else:
                logging.error("No XRDP sink device found")
        except Exception as e:
            logging.error(f"Error setting up output stream: {e}")

    def cleanup(self):
        for stream in self.streams.values():
            if stream:
                try:
                    stream.stop_stream()
                    stream.close()
                except Exception as e:
                    logging.error(f"Error closing stream: {e}")
        self.p.terminate()

class AudioServer:
    def __init__(self, host='0.0.0.0'):
        self.host = host
        self.running = False
        self.clients = set()
        self.audio = AudioHandler()
        
        logging.basicConfig(
            level=logging.INFO,
            format='%(asctime)s - %(levelname)s - %(message)s',
            datefmt='%Y-%m-%d %H:%M:%S'
        )
        
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
                    data = client_socket.recv(CHUNK * 8)  # Increased buffer size for float32
                    if not data:
                        break
                    
                    if self.audio.streams['output']:
                        try:
                            self.audio.streams['output'].write(data, CHUNK)
                        except Exception as e:
                            logging.error(f"Error playing audio: {e}")
                    
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
                        try:
                            data = self.audio.streams['input'].read(CHUNK, exception_on_overflow=False)
                            client_socket.send(data)
                        except Exception as e:
                            logging.error(f"Error reading from input: {e}")
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
