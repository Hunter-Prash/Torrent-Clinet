import socket

ip = '192.168.1.5'
port = 60609  # replace with uTorrent's port

s = socket.socket()
s.settimeout(5)
try:
    s.connect((ip, port))
    print("✅ Port is open and accepting connections!")
except Exception as e:
    print(f"❌ Connection failed: {e}")
finally:
    s.close()
