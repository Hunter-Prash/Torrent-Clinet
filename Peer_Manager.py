import asyncio
import socket
import os
import hashlib
import math # For math.ceil

# --- Configuration and Constants ---
# Removed logging import as requested; using print statements directly.

# BitTorrent Protocol Constants
PROTOCOL_STRING = b'BitTorrent protocol'
PROTOCOL_STRING_LEN = len(PROTOCOL_STRING)
HANDSHAKE_LENGTH = PROTOCOL_STRING_LEN + 1 + 8 + 20 + 20

# BitTorrent Message IDs (after handshake)
MSG_CHOKE = 0
MSG_UNCHOKE = 1
MSG_INTERESTED = 2
MSG_NOT_INTERESTED = 3
MSG_HAVE = 4
MSG_BITFIELD = 5
MSG_REQUEST = 6
MSG_PIECE = 7
MSG_CANCEL = 8
MSG_PORT = 9

# Standard block size for requests
BLOCK_SIZE = 2 ** 14  # 16KB

# --- Global Download State (simulating a "state manager" without a class) ---
# This dictionary will hold all the necessary global info about the torrent and download progress.
# In a real app, this would be structured better, likely with a class.
global_torrent_state = {
    'info_hash': None,            # 20-byte bytes object
    'total_length': 0,            # Total file size
    'piece_length': 0,            # Size of each piece
    'num_pieces': 0,              # Total number of pieces
    'piece_hashes': b'',          # Concatenated SHA1 hashes of all pieces from .torrent file
    'my_bitfield': bytearray(),   # Our client's bitfield (what pieces we have)
    'download_progress': {},      # {piece_idx: bytearray_for_piece_data, ...}
                                  # Or {piece_idx: {'data': bytearray, 'received_blocks': set(), 'total_blocks': int}}
    'outstanding_requests': {},   # {(piece_idx, block_offset): peer_writer_stream_id, ...}
    'peers_active': [],           # List of currently active peers (dicts with reader, writer, state)
    'output_file_path': None,     # Path to the file being downloaded
    'output_file_handle': None,   # File handle for writing
    'downloaded_bytes': 0,        # Total bytes downloaded and verified
    'is_download_complete': False # Flag to stop loops
}











async def send_message(writer: asyncio.StreamWriter, message_id: int, payload: bytes = b''):
    """
    Constructs and sends a BitTorrent message (length prefix + ID + payload).

    Args:
        writer (asyncio.StreamWriter): The writer stream for the peer connection.
        message_id (int): The single-byte message ID.
        payload (bytes): The message payload (can be empty bytes for some messages).
    """
    if writer.is_closing():
        # print(f"Attempted to send message to closing writer.") # Too chatty
        return

    message_length = 1 + len(payload)
    length_prefix_bytes = message_length.to_bytes(4, byteorder='big')
    message_id_byte = bytes([message_id])

    try:
        writer.write(length_prefix_bytes + message_id_byte + payload)
        await writer.drain()
    except Exception as e:
        print(f"Error sending message (ID: {message_id}) to peer: {e}")
        # Mark writer as problematic, should lead to disconnection

















async def read_message(reader: asyncio.StreamReader) -> tuple[int | None, bytes | None]:
    """
    Reads a BitTorrent message from the stream.
    Returns (message_id, payload_bytes) or (None, None) for keep-alive,
    or (False, False) on disconnect/error.
    """
    try:
        # Read the 4-byte length prefix
        length_prefix_bytes = await reader.readexactly(4)
        message_length = int.from_bytes(length_prefix_bytes, byteorder='big')

        if message_length == 0:
            # Keep-alive message
            return None, None

        # Read the message ID (1 byte)
        message_id_byte = await reader.readexactly(1)
        message_id = message_id_byte[0]

        # Read the payload
        payload = b''
        if message_length > 1:
            payload = await reader.readexactly(message_length - 1)

        return message_id, payload

    except asyncio.IncompleteReadError:
        # print("Peer disconnected during message read (IncompleteReadError).") # Too chatty
        return False, False # Signal disconnection
    except asyncio.TimeoutError:
        # print("Timeout during message read from peer.") # Too chatty
        return False, False # Signal disconnection
    except Exception as e:
        # print(f"Error reading message from peer: {e}") # Too chatty for frequent errors
        return False, False # Signal disconnection

















async def perform_handshake(ip: str, port: int, info_hash: bytes, my_peer_id: bytes):
    """
    Establishes a TCP connection to a peer, performs the BitTorrent handshake, and validates the response.

    Args:
        ip (str): The IP address of the peer to connect to.
        port (int): The port number of the peer.
        info_hash (bytes): The 20-byte SHA1 hash of the torrent's info dictionary.
        my_peer_id (bytes): The 20-byte peer ID of our client.

    Returns:
        tuple: (reader, writer, peer_peer_id) if handshake is successful,
               (None, None, None) otherwise.
    """
    peer_addr = f"{ip}:{port}"
    print(f"Attempting to connect to peer: {peer_addr}")

    reader = None
    writer = None

    try:
        # --- 1. Establish TCP Connection ---
        # Use asyncio's open_connection to create a StreamReader and StreamWriter for the peer.
        # Set a timeout to avoid hanging on unresponsive peers.
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection(ip, port),
            timeout=5
        )
        print(f"Successfully connected to peer: {peer_addr}")

        # --- 2. Validate Handshake Inputs ---
        # The info_hash and peer_id must be exactly 20 bytes as per the BitTorrent protocol.
        if not isinstance(info_hash, bytes) or len(info_hash) != 20:
            raise ValueError("info_hash must be a 20-byte bytes object.")
        if not isinstance(my_peer_id, bytes) or len(my_peer_id) != 20:
            raise ValueError("my_peer_id must be a 20-byte bytes object.")

        # --- 3. Construct Handshake Message ---
        # Handshake format:
        # <pstrlen><pstr><reserved><info_hash><peer_id>
        # - pstrlen: 1 byte, length of protocol string (should be 19 for "BitTorrent protocol")
        # - pstr: protocol string ("BitTorrent protocol")
        # - reserved: 8 bytes, all zero (for extension negotiation)
        # - info_hash: 20 bytes, SHA1 hash of the info dict from the .torrent file
        # - peer_id: 20 bytes, unique ID for this client
        handshake_message = (
            bytes([PROTOCOL_STRING_LEN]) +  # pstrlen
            PROTOCOL_STRING +               # pstr
            b'\x00' * 8 +                   # reserved
            info_hash +                     # info_hash
            my_peer_id                      # peer_id
        )

        # --- 4. Send Handshake ---
        writer.write(handshake_message)
        await writer.drain()

        # --- 5. Receive and Parse Peer Handshake ---
        # The handshake response should be exactly HANDSHAKE_LENGTH bytes.
        received_handshake = await asyncio.wait_for(
            reader.readexactly(HANDSHAKE_LENGTH),
            timeout=5
        )

        # --- 6. Validate Handshake Response ---
        # Parse the handshake fields:
        # - First byte: protocol string length
        pstrlen_received = received_handshake[0]
        if pstrlen_received != PROTOCOL_STRING_LEN:
            print(f"Handshake failed with {peer_addr}: Unexpected protocol string length ({pstrlen_received} instead of {PROTOCOL_STRING_LEN}).")
            return None, None, None

        # - Next pstrlen bytes: protocol string
        pstr_received = received_handshake[1 : 1 + pstrlen_received]

        # - Next 8 bytes: reserved (skip)
        # - Next 20 bytes: info_hash
        received_info_hash = received_handshake[1 + pstrlen_received + 8 : 1 + pstrlen_received + 8 + 20]

        # - Next 20 bytes: peer_id
        peer_peer_id = received_handshake[1 + pstrlen_received + 8 + 20 : HANDSHAKE_LENGTH]

        # Check protocol string matches expected
        if pstr_received != PROTOCOL_STRING:
            print(f"Handshake failed with {peer_addr}: Incorrect protocol string.")
            return None, None, None

        # Check info_hash matches the torrent's info_hash (prevents cross-torrent pollution)
        if received_info_hash != info_hash:
            print(f"Handshake failed with {peer_addr}: Info hash mismatch. Expected {info_hash.hex()}, got {received_info_hash.hex()}")
            return None, None, None

        # If all checks pass, handshake is successful
        print(f"Handshake successful with {peer_addr}. Peer ID: {peer_peer_id.hex()}")
        
        return reader, writer, peer_peer_id

    except (asyncio.TimeoutError, ConnectionRefusedError, OSError) as e:
        # Handle connection errors, timeouts, and OS-level socket errors
        print(f"Failed to connect or handshake with {peer_addr}: {e}")
        if writer: writer.close()
        return None, None, None
    except ValueError as e:
        # Handle invalid handshake construction (bad info_hash or peer_id)
        print(f"Error creating handshake for {peer_addr}: {e}")
        if writer: writer.close()
        return None, None, None
    except IndexError as e:
        # Handle unexpected handshake response length/structure
        print(f"Error parsing handshake due to unexpected length or structure from {peer_addr}: {e}. Received data length: {len(received_handshake) if 'received_handshake' in locals() else 'N/A'}")
        if writer: writer.close()
        return None, None, None
    except Exception as e:
        # Catch-all for any other unexpected errors
        print(f"An unexpected error occurred with {peer_addr}: {e}")
        if writer: writer.close()
        return None, None, None


















async def handle_peer_initial_messages(
    ip: str,
    port: int,
    reader: asyncio.StreamReader,
    writer: asyncio.StreamWriter,
    peer_peer_id: bytes,
    torrent_num_pieces: int
) -> dict | None:
    """
    Handles the initial message exchange with a peer after a successful handshake.
    Returns a peer_state dictionary on success, None on failure.
    """
    peer_addr = f"{ip}:{port}"
    peer_state = {
        'ip': ip,
        'port': port,
        'reader': reader,
        'writer': writer,
        'peer_id': peer_peer_id,
        'peer_choking': True,        # Initially, peer chokes us
        'peer_interested': False,    # Initially, peer is not interested in us
        'am_choking': True,          # Initially, we choke the peer
        'am_interested': False,      # FIX: Corrected typo from 'am_intrested' to 'am_interested'
        'peer_bitfield': None,       # To store the bitfield received from the peer
        'last_activity': asyncio.get_event_loop().time() # For keeping track of active connections
    }

    try:
        print(f"[{peer_addr}] Waiting for initial message (bitfield/choke/unchoke)...")
        # Use a short timeout for initial read; could be keep-alive or slow peer
        message_id, payload = await asyncio.wait_for(read_message(reader), timeout=10)

        if message_id == MSG_BITFIELD:
            expected_bitfield_len = (torrent_num_pieces + 7) // 8
            if len(payload) == expected_bitfield_len:
                peer_state['peer_bitfield'] = bytearray(payload) # FIX: Convert payload to bytearray for mutability
                print(f"[{peer_addr}] Received bitfield. Length: {len(payload)} bytes.")
                # We can now potentially become 'interested'
            else:
                print(f"[{peer_addr}] Received malformed bitfield (unexpected length: {len(payload)}). Disconnecting.")
                return None
            
        elif message_id == MSG_CHOKE:
            peer_state['peer_choking'] = True
            print(f"[{peer_addr}] Received CHOKE message.")
        elif message_id == MSG_UNCHOKE:
            peer_state['peer_choking'] = False
            print(f"[{peer_addr}] Received UNCHOKE message.")
        elif message_id == MSG_INTERESTED:
            peer_state['peer_interested'] = True
            print(f"[{peer_addr}] Received INTERESTED message.")
        elif message_id == MSG_NOT_INTERESTED:
            peer_state['peer_interested'] = False
            print(f"[{peer_addr}] Received NOT_INTERESTED message.")
        elif message_id is None and payload is None: # Keep-alive
            print(f"[{peer_addr}] Received KEEP-ALIVE message.")
        elif message_id is False and payload is False: # Disconnection signal from read_message
            print(f"[{peer_addr}] Peer disconnected during initial message exchange.")
            return None
        else:
            print(f"[{peer_addr}] Received unexpected initial message (ID: {message_id}).")

        # --- 2. Send Our Bitfield ---
        # Our client starts with no pieces, so send an all-zero bitfield.
        # global_torrent_state['my_bitfield'] is already initialized as bytearray() in global_torrent_state
        # and its size is set in main_peer_connection_phase
        
        await send_message(writer, MSG_BITFIELD, global_torrent_state['my_bitfield'])
        print(f"[{peer_addr}] Sent our bitfield. Length: {len(global_torrent_state['my_bitfield'])} bytes.")

        # --- 3. Determine and Send Interested Message ---
        # For now, we become interested if the peer has any piece we don't have.
        # A simple check: if their bitfield is not empty (they have *something*) AND
        # it's not the same as our all-zero bitfield.
        if peer_state['peer_bitfield'] and peer_state['peer_bitfield'] != global_torrent_state['my_bitfield']:
            await send_message(writer, MSG_INTERESTED)
            peer_state['am_interested'] = True # FIX: Corrected typo from 'am_intrested' to 'am_interested'
            print(f"[{peer_addr}] Sent INTERESTED message.")
        else:
            print(f"[{peer_addr}] Not sending INTERESTED message (no interesting pieces or peer's bitfield not received yet).")

        peer_state['last_activity'] = asyncio.get_event_loop().time() # Update activity time
        return peer_state

    except asyncio.TimeoutError:
        print(f"[{peer_addr}] Timeout waiting for initial message exchange.")
        if writer: writer.close()
        return None
    except Exception as e:
        print(f"Error during initial message exchange with {peer_addr}: {e}")
        if writer: writer.close()
        return None
















def get_next_block_request(peer_state: dict) -> tuple[int, int, int] | None:
    """
    Determines the next block to request from a peer.
    Very basic logic for now: finds the first missing piece the peer has,
    then the first missing block in that piece.
    """
    # FIX: Corrected typo from 'am_intrested' to 'am_interested'
    if peer_state['peer_choking'] or not peer_state['am_interested']:
        return None # Cannot request if choked or not interested

    if not peer_state['peer_bitfield']:
        return None # Cannot request if we don't know what peer has

    num_pieces = global_torrent_state['num_pieces']
    piece_length = global_torrent_state['piece_length']
    total_length = global_torrent_state['total_length']

    for piece_idx in range(num_pieces):
        # Check if we DON'T have this piece
        # FIX: Ensure global_torrent_state['my_bitfield'] is large enough to access piece_idx
        if (piece_idx // 8) >= len(global_torrent_state['my_bitfield']):
            # This can happen if num_pieces is miscalculated or my_bitfield isn't fully sized.
            # For robust download, we should extend my_bitfield here if needed,
            # but for now, we'll skip if it's out of bounds.
            continue 
            
        if not (global_torrent_state['my_bitfield'][piece_idx // 8] >> (7 - (piece_idx % 8))) & 1:
            # Check if peer HAS this piece
            # FIX: Ensure peer_state['peer_bitfield'] is large enough
            if (piece_idx // 8) >= len(peer_state['peer_bitfield']):
                continue # Peer bitfield doesn't cover this piece, assume they don't have it

            if (peer_state['peer_bitfield'][piece_idx // 8] >> (7 - (piece_idx % 8))) & 1:
                # This is a piece we need and the peer has!
                
                # Ensure a buffer exists for this piece
                if piece_idx not in global_torrent_state['download_progress']:
                    # Calculate actual length of this piece (last piece might be shorter)
                    current_piece_len = piece_length
                    if piece_idx == num_pieces - 1: # Last piece
                        current_piece_len = total_length % piece_length
                        if current_piece_len == 0 and total_length > 0: # If total_length is exact multiple of piece_length
                            current_piece_len = piece_length
                        elif total_length == 0: # Handle zero-length files
                            current_piece_len = 0
                    
                    if current_piece_len == 0: # Avoid creating 0-byte piece buffer
                        continue # Skip zero-length pieces

                    total_blocks_in_piece = math.ceil(current_piece_len / BLOCK_SIZE)
                    global_torrent_state['download_progress'][piece_idx] = {
                        'data': bytearray(current_piece_len),
                        'received_blocks': [False] * total_blocks_in_piece,
                        'total_blocks': total_blocks_in_piece
                    }
                    print(f"[{peer_state['ip']}:{peer_state['port']}] Initiating download for piece {piece_idx} ({current_piece_len} bytes) with {total_blocks_in_piece} blocks.")


                # Find the next missing block in this piece
                piece_info = global_torrent_state['download_progress'][piece_idx]
                for block_offset_idx in range(piece_info['total_blocks']):
                    block_start_byte_offset = block_offset_idx * BLOCK_SIZE
                    
                    # Check if this block is already received or currently outstanding
                    if not piece_info['received_blocks'][block_offset_idx] and \
                       (piece_idx, block_start_byte_offset) not in global_torrent_state['outstanding_requests']:
                        
                        block_length = BLOCK_SIZE
                        # Adjust block_length for the very last block of the piece if it's smaller
                        if block_start_byte_offset + BLOCK_SIZE > len(piece_info['data']):
                            block_length = len(piece_info['data']) - block_start_byte_offset
                        
                        return piece_idx, block_start_byte_offset, block_length
    return None # No suitable block found to request
















async def verify_and_save_piece(piece_index: int, piece_data: bytearray):
    """
    Verifies a downloaded piece's hash and saves it to disk.
    If valid, updates my_bitfield and informs other peers.
    """
    piece_start_offset = piece_index * global_torrent_state['piece_length']
    
    # Get the expected hash for this piece
    expected_hash = global_torrent_state['piece_hashes'][piece_index * 20 : (piece_index + 1) * 20]
    
    # Calculate the hash of the downloaded piece
    calculated_hash = hashlib.sha1(piece_data).digest()

    if calculated_hash == expected_hash:
        print(f"Verified piece {piece_index}: Hash MATCHES.")
        try:
            # Write piece to disk
            if not global_torrent_state['output_file_handle']:
                print("Error: Output file handle is not open!")
                return
            
            # Ensure the file is large enough or seek to correct position
            global_torrent_state['output_file_handle'].seek(piece_start_offset)
            global_torrent_state['output_file_handle'].write(piece_data)
            global_torrent_state['output_file_handle'].flush() # Ensure data is written
            
            # Update my_bitfield - This is a bytearray, so item assignment works
            byte_idx = piece_index // 8
            bit_offset = 7 - (piece_index % 8) # Bits are MSB first in standard BitTorrent bitfield
            
            # Ensure my_bitfield is large enough to set the bit
            while byte_idx >= len(global_torrent_state['my_bitfield']):
                global_torrent_state['my_bitfield'].append(0)

            global_torrent_state['my_bitfield'][byte_idx] |= (1 << bit_offset)
            
            global_torrent_state['downloaded_bytes'] += len(piece_data)
            download_percentage = (global_torrent_state['downloaded_bytes'] / global_torrent_state['total_length']) * 100
            print(f"Piece {piece_index} saved to disk. Total downloaded: {global_torrent_state['downloaded_bytes']}/{global_torrent_state['total_length']} bytes ({download_percentage:.2f}%)")

            # Inform other peers that we have this piece
            # Needs to be done concurrently for all active peers
            have_payload = piece_index.to_bytes(4, byteorder='big')
            for peer_state in list(global_torrent_state['peers_active']): # Iterate a copy to avoid modification issues
                if peer_state['writer'] and not peer_state['writer'].is_closing():
                    asyncio.create_task(send_message(peer_state['writer'], MSG_HAVE, have_payload))
            # print(f"Sent HAVE message for piece {piece_index} to active peers.") # Too chatty

            # Check for download completion
            if global_torrent_state['downloaded_bytes'] == global_torrent_state['total_length']:
                global_torrent_state['is_download_complete'] = True
                print("\n\n#############################################")
                print("#### TORRENT DOWNLOAD COMPLETE! CONGRATS! ####")
                print("#############################################\n\n")

        except Exception as e:
            print(f"Error saving piece {piece_index} or updating state: {e}")
            # Potentially mark piece as bad/re-download needed
    else:
        print(f"Verified piece {piece_index}: Hash MISMATCH. Discarding and re-downloading.")
        # Clear the download progress for this piece so it can be re-requested
        if piece_index in global_torrent_state['download_progress']:
            del global_torrent_state['download_progress'][piece_index]
        # Also clear any outstanding requests for this piece
        # It's safer to recreate outstanding_requests for the piece by iterating and rebuilding
        keys_to_delete = [
            key for key in global_torrent_state['outstanding_requests']
            if key[0] == piece_index
        ]
        for key in keys_to_delete:
            del global_torrent_state['outstanding_requests'][key]
















async def peer_download_loop(peer_state: dict):
    """
    Main loop for continuous message exchange and block requesting/receiving for a single peer.
    """
    ip, port = peer_state['ip'], peer_state['port']
    peer_addr = f"{ip}:{port}"
    reader, writer = peer_state['reader'], peer_state['writer']

    print(f"[{peer_addr}] Starting download loop...")

    try:
        while not global_torrent_state['is_download_complete'] and not writer.is_closing():
            peer_state['last_activity'] = asyncio.get_event_loop().time() # Update activity timestamp

            # 1. Try to request a block if conditions are met
            # Only request if not choked by peer and we are interested
            if not peer_state['peer_choking'] and peer_state['am_interested']: # FIX: Corrected typo from 'am_intrested'
                request_info = get_next_block_request(peer_state)
                if request_info:
                    piece_idx, block_offset, block_length = request_info
                    request_payload = (
                        piece_idx.to_bytes(4, byteorder='big') +
                        block_offset.to_bytes(4, byteorder='big') +
                        block_length.to_bytes(4, byteorder='big')
                    )
                    await send_message(writer, MSG_REQUEST, request_payload)
                    # Use peer's address as a unique identifier for writer in outstanding_requests
                    global_torrent_state['outstanding_requests'][(piece_idx, block_offset)] = peer_addr
                    # print(f"[{peer_addr}] Requested piece {piece_idx}, offset {block_offset}, length {block_length}.") # Too chatty
                # else:
                #     print(f"[{peer_addr}] No blocks to request currently.") # Too chatty


            # 2. Read incoming messages
            # Use a short timeout to allow for requesting if no messages are immediately available
            message_id, payload = await asyncio.wait_for(read_message(reader), timeout=5) # Increased read timeout slightly

            if message_id is False and payload is False: # Disconnection signal
                print(f"[{peer_addr}] Peer disconnected or error in read_message. Exiting loop.")
                break # Exit loop on disconnect

            peer_state['last_activity'] = asyncio.get_event_loop().time() # Update activity timestamp

            if message_id == MSG_CHOKE:
                peer_state['peer_choking'] = True
                print(f"[{peer_addr}] Received CHOKE.")
            elif message_id == MSG_UNCHOKE:
                peer_state['peer_choking'] = False
                print(f"[{peer_addr}] Received UNCHOKE.")
            elif message_id == MSG_INTERESTED:
                peer_state['peer_interested'] = True
                print(f"[{peer_addr}] Received INTERESTED.")
            elif message_id == MSG_NOT_INTERESTED:
                peer_state['peer_interested'] = False
                print(f"[{peer_addr}] Received NOT_INTERESTED.")
            elif message_id == MSG_HAVE:
                have_piece_idx = int.from_bytes(payload, byteorder='big')
                # Update peer's bitfield (important for future requests)
                if peer_state['peer_bitfield']: # Ensure peer_bitfield is initialized
                    byte_idx = have_piece_idx // 8
                    bit_offset = 7 - (have_piece_idx % 8)
                    # Ensure bitfield can accommodate the new piece index
                    # FIX: Extend peer_state['peer_bitfield'] (which is now bytearray)
                    while len(peer_state['peer_bitfield']) * 8 <= have_piece_idx:
                        peer_state['peer_bitfield'].append(0) # Append zeros
                    peer_state['peer_bitfield'][byte_idx] |= (1 << bit_offset)
                print(f"[{peer_addr}] Received HAVE for piece {have_piece_idx}.")
                # If we are not interested, and now peer has a piece we need, send INTERESTED
                # Only send INTERESTED if we don't have the piece yet
                if not peer_state['am_interested'] and \
                   (have_piece_idx // 8) < len(global_torrent_state['my_bitfield']) and \
                   not ((global_torrent_state['my_bitfield'][have_piece_idx // 8] >> (7 - (have_piece_idx % 8))) & 1):
                    await send_message(writer, MSG_INTERESTED)
                    peer_state['am_interested'] = True # FIX: Corrected typo from 'am_intrested'
                    print(f"[{peer_addr}] Sent INTERESTED message after HAVE.")

            elif message_id == MSG_BITFIELD:
                peer_state['peer_bitfield'] = bytearray(payload) # FIX: Convert payload to bytearray for mutability
                print(f"[{peer_addr}] Received BITFIELD (again). Length: {len(payload)}.")
            elif message_id == MSG_REQUEST:
                req_piece_idx = int.from_bytes(payload[0:4], byteorder='big')
                req_block_offset = int.from_bytes(payload[4:8], byteorder='big')
                req_block_length = int.from_bytes(payload[8:12], byteorder='big')
                # print(f"[{peer_addr}] Received REQUEST for piece {req_piece_idx}, offset {req_block_offset}, length {req_block_length}. (Handling in Phase 2)")
            elif message_id == MSG_PIECE:
                piece_idx = int.from_bytes(payload[0:4], byteorder='big')
                block_offset = int.from_bytes(payload[4:8], byteorder='big')
                block_data = payload[8:]

                request_key = (piece_idx, block_offset)
                if request_key in global_torrent_state['outstanding_requests']:
                    del global_torrent_state['outstanding_requests'][request_key]
                
                if piece_idx not in global_torrent_state['download_progress']:
                    print(f"[{peer_addr}] Received PIECE for uninitialized piece buffer {piece_idx}. Skipping.")
                    continue

                piece_info = global_torrent_state['download_progress'][piece_idx]
                if block_offset + len(block_data) > len(piece_info['data']):
                    print(f"[{peer_addr}] Received PIECE with out-of-bounds data for piece {piece_idx}, offset {block_offset}, len {len(block_data)}. Skipping.")
                    continue

                piece_info['data'][block_offset : block_offset + len(block_data)] = block_data
                block_offset_idx = block_offset // BLOCK_SIZE
                if block_offset_idx < len(piece_info['received_blocks']):
                    piece_info['received_blocks'][block_offset_idx] = True
                    # print(f"[{peer_addr}] Received block for piece {piece_idx}, offset {block_offset}.") # Too chatty
                else:
                    print(f"[{peer_addr}] Error: block_offset_idx out of bounds for piece_info['received_blocks'].")

                if all(piece_info['received_blocks']):
                    print(f"[{peer_addr}] All blocks for piece {piece_idx} received. Verifying...")
                    await verify_and_save_piece(piece_idx, piece_info['data'])
                    # After verification (success or failure), clear this piece from download_progress
                    # to free memory and allow for re-download if needed.
                    if piece_idx in global_torrent_state['download_progress']:
                        del global_torrent_state['download_progress'][piece_idx]

            elif message_id is None and payload is None: # Keep-alive
                pass # Already handled, just keep loop going
            else:
                print(f"[{peer_addr}] Received unknown message ID: {message_id} with payload length {len(payload)}.")

            await asyncio.sleep(0.001) # Very short sleep to yield control frequently

    except Exception as e:
        print(f"[{peer_addr}] Error in peer_download_loop: {e}")
    finally:
        print(f"[{peer_addr}] Closing connection (loop ended).")
        if writer and not writer.is_closing():
            writer.close()
            await writer.wait_closed()
        # Remove peer from active list
        global_torrent_state['peers_active'] = [
            p for p in global_torrent_state['peers_active'] if p['peer_id'] != peer_state['peer_id']
        ]
        # print(f"Remaining active peers: {len(global_torrent_state['peers_active'])}") # Too chatty













async def main_peer_connection_phase(peers: list, info_hash: bytes, my_peer_id: bytes, torrent_info: dict):
    """
    Main function to orchestrate peer connections, handshakes, initial message exchange,
    and start download loops.
    """
    print("Starting Phase 1, Part 1 & 2: Connecting and Initial Exchange.")

    # Initialize global torrent state from parsed torrent info
    global_torrent_state['info_hash'] = info_hash
    global_torrent_state['total_length'] = torrent_info[b'length']
    global_torrent_state['piece_length'] = torrent_info[b'piece length']
    global_torrent_state['num_pieces'] = torrent_info['num_pieces']
    global_torrent_state['piece_hashes'] = torrent_info[b'pieces']
    global_torrent_state['output_file_path'] = torrent_info[b'name'].decode('utf-8')
    
    # Initialize my_bitfield to the correct size (all zeros initially)
    global_torrent_state['my_bitfield'] = bytearray((global_torrent_state['num_pieces'] + 7) // 8) # FIX: Use bytearray directly

    # Open output file for writing (binary mode, create if not exists, truncate if exists)
    try:
        # Check if file exists, if not, create dummy file to ensure path is valid
        if not os.path.exists(global_torrent_state['output_file_path']):
            # For large files, creating it with 'wb' implicitly truncates.
            # Using 'x' to ensure it's a new file, then 'r+b'
            try:
                # FIX: Ensure parent directories exist
                os.makedirs(os.path.dirname(global_torrent_state['output_file_path']) or '.', exist_ok=True)
                with open(global_torrent_state['output_file_path'], 'xb') as temp_f:
                    temp_f.truncate(global_torrent_state['total_length']) # Pre-allocate
            except FileExistsError:
                pass # File already exists, proceed to open with 'r+b'

        global_torrent_state['output_file_handle'] = open(global_torrent_state['output_file_path'], 'r+b')
        # Seek to end to ensure we don't accidentally truncate if file existed before
        # global_torrent_state['output_file_handle'].seek(0, os.SEEK_END)
        print(f"Opened output file: {global_torrent_state['output_file_path']} for writing.")
    except Exception as e:
        print(f"FATAL: Could not open output file {global_torrent_state['output_file_path']}: {e}")
        return # Cannot proceed without file

    handshake_tasks = []
    for ip, port in peers:
        handshake_tasks.append(perform_handshake(ip, port, info_hash, my_peer_id))

    handshake_results = await asyncio.gather(*handshake_tasks, return_exceptions=False)

    connected_peers_for_exchange = []
    for i, (reader, writer, peer_peer_id) in enumerate(handshake_results):
        if reader and writer and peer_peer_id:
            connected_peers_for_exchange.append({
                'ip': peers[i][0],
                'port': peers[i][1],
                'reader': reader,
                'writer': writer,
                'peer_id': peer_peer_id,
            })
        else:
            print(f"Failed to handshake with {peers[i][0]}:{peers[i][1]}.")

    print(f"Successfully handshaked with {len(connected_peers_for_exchange)} peers.")

    exchange_tasks = []
    for peer_info_dict in connected_peers_for_exchange:
        exchange_tasks.append(
            handle_peer_initial_messages(
                peer_info_dict['ip'],
                peer_info_dict['port'],
                peer_info_dict['reader'],
                peer_info_dict['writer'],
                peer_info_dict['peer_id'],
                torrent_info['num_pieces']
            )
        )

    exchange_results = await asyncio.gather(*exchange_tasks, return_exceptions=False)

    # Populate global_torrent_state['peers_active']
    for peer_state in exchange_results:
        if peer_state:
            global_torrent_state['peers_active'].append(peer_state)
        else:
            pass # Error message already printed by handle_peer_initial_messages

    print(f"Finished initial exchange. Active peers ready for piece requests: {len(global_torrent_state['peers_active'])}")

    # --- Start Phase 1, Part 3: Main Download Loop ---
    if not global_torrent_state['peers_active']:
        print("No active peers to download from. Exiting.")
        if global_torrent_state['output_file_handle'] and not global_torrent_state['output_file_handle'].closed:
            global_torrent_state['output_file_handle'].close()
        return

    download_loop_tasks = []
    for peer_state in global_torrent_state['peers_active']:
        download_loop_tasks.append(peer_download_loop(peer_state))

    # Keep track of active tasks for graceful shutdown
    all_running_tasks = asyncio.gather(*download_loop_tasks, return_exceptions=True)

    try:
        # Await all download loops or until download is complete
        # We need a way to stop this if download is complete.
        # This will run until all peer_download_loop tasks naturally finish or hit error.
        # For a long download, you'd want a separate monitor task.
        await all_running_tasks 
    except asyncio.CancelledError:
        print("Download loops cancelled.")
    finally:
        # Final cleanup after download completes or all peers disconnect
        print("All download loops finished (or were cancelled).")
        if global_torrent_state['output_file_handle'] and not global_torrent_state['output_file_handle'].closed:
            global_torrent_state['output_file_handle'].close()
            print("Output file closed.")

        if global_torrent_state['is_download_complete']:
            print("Download finished successfully!")
        else:
            print("Download finished but not complete. Check logs for issues or more peers.")



