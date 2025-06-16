import asyncio
import os
import time
import base64

# Import functions from your parser.py
from parser import parse_torrent_file, getinfo_hash

# Import functions from your tracker.py
from tracker import get_peers, getpeerID

# Import the main peer connection phase from your Peer_Manager.py
from Peer_Manager import main_peer_connection_phase

# --- Main Execution Block ---
if __name__ == "__main__":
    # Define the path to your torrent file
    torrent_path = "C:\\Users\\pctec\\Downloads\\enwiki-20241201-pages-articles-multistream.xml.bz2-87098feed22fd5848964c6fc87de09cea94f020d.torrent"
    
    # 1. Parse the torrent file
    # 'info' will be the decoded dictionary (keys and string values),
    # 'announce_url' will be the tracker URL.
    info, announce_url = parse_torrent_file(torrent_path) 
    
    
    # 2. Get the info_hash (20-byte bytes object)
    # This is crucial for the BitTorrent handshake.
    info_hash = getinfo_hash(torrent_path) 
    #print(info_hash.hex())
    #print(base64.b64encode(info_hash).decode())
   # time.sleep(30)

   
    # 3. Generate your client's unique peer ID (20-byte bytes object)
    peerId = getpeerID() 

    # 4. Calculate total_length of the torrent
    # Your parser decodes 'length' for single files and handles 'files' for multi-file.
    if 'length' in info: # Single-file torrent
        total_length = info['length']
    elif 'files' in info: # Multi-file torrent
        total_length = sum(f['length'] for f in info['files'] if 'length' in f)
    else:
        print("Error: Could not determine total length of the torrent.")
        exit() # Cannot proceed without total length

    # 5. Calculate the total number of pieces
    # 'pieces' from your parser is already handled by decode_value,
    # and for the 'pieces' field, it should correctly remain a bytes object
    # because SHA1 hashes are not valid UTF-8.
    if 'pieces' in info and isinstance(info['pieces'], bytes):
        torrent_num_pieces = len(info['pieces']) // 20
    else:
        print("Error: 'pieces' key missing or malformed in torrent info.")
        exit() # Cannot proceed without piece hashes

    # 6. Prepare the torrent_info dictionary for Peer_Manager
    # The Peer_Manager expects certain keys to be bytes (b'key')
    # and the corresponding values to be in their raw (bytes/int) format.
    torrent_info_for_peer_manager = {
        # 'name' comes as a string from parser, re-encode it to bytes
        b'name': info.get('name', 'unknown_file').encode('utf-8'), 
        # 'length' is already an integer, map it to b'length' key
        b'length': total_length, 
        # 'piece length' is an integer from parser, map it to b'piece length' key
        b'piece length': info['piece length'], 
        # 'pieces' is already a bytes object from parser, map it to b'pieces' key
        b'pieces': info['pieces'], 
        # 'num_pieces' is an integer, keep this key as a string for simplicity in Peer_Manager
        'num_pieces': torrent_num_pieces 
    }

    # 7. Get the list of peers from the tracker
    # Ensure your get_peers function correctly handles the announce_url and other parameters.
    peer_list = get_peers(announce_url, info_hash, peerId, total_length)
    print(f"\nReceived {len(peer_list)} peers from tracker.")
    print(peer_list) # Uncomment to see the list of peers

    # 8. Start the main peer connection and download phase
    try:
        asyncio.run(
            main_peer_connection_phase(
                peer_list,                 # List of (ip, port) tuples from the tracker
                info_hash,                 # 20-byte torrent info hash
                peerId,                    # Your client's 20-byte peer ID
                torrent_info_for_peer_manager # The prepared dictionary with torrent details
            )
        ) 
    except KeyboardInterrupt:
        print("\nClient stopped by user (KeyboardInterrupt).")
    except Exception as e:
        print(f"An unhandled error occurred in main: {e}")
        