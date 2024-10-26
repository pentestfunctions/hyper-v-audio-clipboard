#!/usr/bin/env python3
import socket
import threading
import pyaudio
import time
import logging
from queue import Queue
import signal
import sys
import subprocess
import os
import pwd

# Audio Configuration
CHUNK = 1024
FORMAT = pyaudio.paInt16
CHANNELS = 2
RATE = 44100
AUDIO_PORT = 5001

def get_user():
    """Get the actual username even when running with sudo"""
    return pwd.getpwuid(int(os.environ.get('SUDO_UID', os.getuid()))).pw_name

def setup_audio_services():
    """Initialize and restart audio services for Kali Linux"""
    try:
        logging.info("Setting up audio services...")
        username = get_user()
        
        # Stop any existing PulseAudio processes
        try:
            subprocess.run(['pulseaudio', '--kill'], stderr=subprocess.PIPE)
            time.sleep(1)
        except:
            pass

        # Make sure the PulseAudio directory exists
        pulse_dir = f"/home/{username}/.config/pulse"
        os.makedirs(pulse_dir, exist_ok=True)
        
        # Set proper ownership if running as root
        if os.geteuid() == 0:
            uid = int(os.environ.get('SUDO_UID', os.getuid()))
            gid = int(os.environ.get('SUDO_GID', os.getgid()))
            os.chown(pulse_dir, uid, gid)

        # Start PulseAudio server
        try:
            env = os.environ.copy()
            if os.geteuid() == 0:
                env['HOME'] = f"/home/{username}"
                env['USER'] = username
                env['PULSE_RUNTIME_PATH'] = f"/run/user/{uid}/pulse"
            
            start_cmd = ['pulseaudio', '--start', '--log-target=syslog']
            subprocess.run(start_cmd, env=env, stderr=subprocess.PIPE)
            time.sleep(2)
            
            # Load required modules
            subprocess.run(['pacmd', 'load-module', 'module-null-sink'], stderr=subprocess.PIPE)
            time.sleep(1)
        except Exception as e:
            logging.error(f"Error starting PulseAudio: {e}")
            raise

        # Verify audio system is working
        max_attempts = 3
        for attempt in range(max_attempts):
            try:
                subprocess.run(['pactl', 'info'], check=True, capture_output=True)
                logging.info("Audio services initialized successfully")
                return
            except subprocess.CalledProcessError:
                if attempt < max_attempts - 1:
                    logging.warning(f"PulseAudio not ready, attempt {attempt + 1}/{max_attempts}")
                    time.sleep(2)
                else:
                    logging.error("Failed to verify audio system after multiple attempts")
                    raise
            
    except Exception as e:
        logging.error(f"Error setting up audio services: {e}")
        raise

class AudioHandler:
    def __init__(self):
        self.p = pyaudio.PyAudio()
        self.streams = {'input': None, 'output': None}
        
    def setup_streams(self):
        """Setup audio streams using default PulseAudio devices"""
        try:
            # Make sure audio services are running
            setup_audio_services()
            
            # List available devices for debugging
            self.list_devices()
            
            # Find the PulseAudio device
            pulse_input_index = None
            pulse_output_index = None
            
            for i in range(self.p.get_device_count()):
                try:
                    device_info = self.p.get_device_info_by_index(i)
                    if 'pulse' in device_info['name'].lower():
                        if device_info['maxInputChannels'] > 0:
                            pulse_input_index = i
                        if device_info['maxOutputChannels'] > 0:
                            pulse_output_index = i
                except Exception as e:
                    logging.error(f"Error checking device {i}: {e}")

            if pulse_input_index is not None:
                self.streams['input'] = self.p.open(
                    format=FORMAT,
                    channels=CHANNELS,
                    rate=RATE,
                    input=True,
                    input_device_index=pulse_input_index,
                    frames_per_buffer=CHUNK
                )
                logging.info("Input stream setup successfully")
            else:
                logging.error("No PulseAudio input device found")
                
            if pulse_output_index is not None:
                self.streams['output'] = self.p.open(
                    format=FORMAT,
                    channels=CHANNELS,
                    rate=RATE,
                    output=True,
                    output_device_index=pulse_output_index,
                    frames_per_buffer=CHUNK
                )
                logging.info("Output stream setup successfully")
            else:
                logging.error("No PulseAudio output device found")
            
        except Exception as e:
            logging.error(f"Error setting up streams: {e}")
            raise

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
        logging.info("Available audio devices:\n" + '\n'.join(info))

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
        self.server_socket = None
        
        # Configure logging
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
        sys.exit(0)

    def handle_client(self, client_socket, address):
        logging.info(f"New client connected from {address}")
        self.clients.add(client_socket)
        
        def receive_audio():
            while self.running:
                try:
                    data = client_socket.recv(CHUNK * 4)
                    if not data:
                        break
                    
                    if self.audio.streams['output']:
                        try:
                            self.audio.streams['output'].write(data)
                        except Exception as e:
                            logging.error(f"Error playing audio: {e}")
                            break
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
                            break
                except Exception as e:
                    logging.error(f"Error sending audio to {address}: {e}")
                    break

        receive_thread = threading.Thread(target=receive_audio)
        send_thread = threading.Thread(target=send_audio)
        
        receive_thread.start()
        send_thread.start()
        
        receive_thread.join()
        send_thread.join()
        
        if client_socket in self.clients:
            self.clients.remove(client_socket)
        try:
            client_socket.close()
        except:
            pass
        logging.info(f"Client {address} disconnected")

    def start(self):
        try:
            # Initialize audio services and streams
            self.audio.setup_streams()
            
            # Create and setup server socket
            self.server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            
            try:
                self.server_socket.bind((self.host, AUDIO_PORT))
                self.server_socket.listen(5)
                logging.info(f"Audio server listening on {self.host}:{AUDIO_PORT}")
                
                self.running = True
                
                while self.running:
                    try:
                        client_socket, address = self.server_socket.accept()
                        client_thread = threading.Thread(
                            target=self.handle_client,
                            args=(client_socket, address)
                        )
                        client_thread.start()
                    except socket.error as e:
                        if self.running:  # Only log if we're still meant to be running
                            logging.error(f"Socket accept error: {e}")
                    except Exception as e:
                        if self.running:
                            logging.error(f"Error accepting connection: {e}")
                
            except Exception as e:
                logging.error(f"Server socket error: {e}")
                raise
                
        except Exception as e:
            logging.error(f"Server error: {e}")
        finally:
            self.stop()

    def stop(self):
        self.running = False
        
        # Close all client connections
        for client in self.clients.copy():  # Use copy to avoid modification during iteration
            try:
                client.close()
            except:
                pass
        self.clients.clear()
        
        # Close server socket
        if self.server_socket is not None:
            try:
                self.server_socket.close()
            except:
                pass
            self.server_socket = None
            
        # Cleanup audio
        try:
            self.audio.cleanup()
        except:
            pass

if __name__ == "__main__":
    # Check if root and handle permissions
    if os.geteuid() != 0:
        logging.error("This script must be run with sudo to properly initialize audio services")
        sys.exit(1)

    try:
        server = AudioServer()
        server.start()
    except Exception as e:
        logging.error(f"Fatal error: {e}")
        sys.exit(1)
    except KeyboardInterrupt:
        logging.info("Keyboard interrupt received, shutting down...")
        sys.exit(0)
