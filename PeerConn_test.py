import asyncio
import socket
import os

#torrent protocol constants
PROTOCOL_STRING=b'BitTorrent protocol'
PROTOCOL_STRING_LEN = len(PROTOCOL_STRING)
HANDSHAKE_LENGTH = PROTOCOL_STRING_LEN + 1 + 8 + 20 + 20 # pstrlen + pstr + reserved + info_hash + peer_id

#  FNC TO PERFORM HANDSHAKE WITH PEERS

async def perform_handshake(ip:str,port:int,info_hash:bytes,my_peer_id:bytes):
    """
        Establishes a TCP connection, performs the handshake, and validates it.

    Args:
        ip (str): The IP address of the peer.
        port (int): The port of the peer.
        info_hash (bytes): The 20-byte SHA1 hash of the torrent's info dictionary.
                           MUST be a bytes object.
        my_peer_id (bytes): Your client's 20-byte peer ID. MUST be a bytes object.

    Returns:
        tuple: (asyncio.StreamReader, asyncio.StreamWriter, bytes) if handshake is successful.
               (None, None, None) otherwise.
    """

    peer_addr=f'{ip}:{port}'
    print(f'Attempting to connect to peer:{peer_addr}')
    reader = None
    writer = None

    try:
        #1 ESTABLISH TCP CONNECTION
        reader,writer=await asyncio.wait_for(asyncio.open_connection(ip,port),timeout=5)
        print(f"Successfully connected to peer: {peer_addr}")

        #  CREATE HANDSHAKE MESSAGE USING BYTE CONCATENATION
        # ENSURE INFO_HASH AND MY_PEER_ID ARE CORRECT TYPES AND LENGTHS
        if not isinstance(info_hash,bytes) or len(info_hash)!=20:
            raise ValueError('info_hash must be a 20-byte bytes object.')
        if not isinstance(my_peer_id,bytes) or len(my_peer_id)!=20:
            raise ValueError('my_peer_id must be a 20-byte bytes object.')
        
        handshake_mssg=(
            bytes([PROTOCOL_STRING_LEN]) + # pstrlen (1 byte)
            PROTOCOL_STRING +             # pstr (19 bytes)
            b'\x00' * 8 +                 # reserved (8 bytes)
            info_hash +                   # info_hash (20 bytes)
            my_peer_id                    # peer_id (20 bytes)
        )

        #2 SEND HANDSHAKE MSSG
        writer.write(handshake_mssg)
        await writer.drain()#ensure data is sent

        #3 RECEIVE HANDSHAKE MSSG FROM PEER (with a timeout)
        received_handshake=await asyncio.wait_for(reader.readexactly(HANDSHAKE_LENGTH),timeout=5)

        # 4. Parse received handshake using byte slicing
        # The handshake is 68 bytes long with fixed segments:
        # 1 byte: pstrlen
        # 19 bytes: pstr ("BitTorrent protocol")
        # 8 bytes: reserved
        # 20 bytes: info_hash
        # 20 bytes: peer_id

        pstrlen = received_handshake[0]
        # Verify pstrlen early to avoid index errors if it's unexpected
        if pstrlen != PROTOCOL_STRING_LEN:
            print(f"Handshake failed with {peer_addr}: Unexpected protocol string length ({pstrlen} instead of {PROTOCOL_STRING_LEN}).") # Replaced logging.warning
            return None, None, None

        pstr = received_handshake[1 : 1 + pstrlen]
        # reserved = received_handshake[1 + pstrlen : 1 + pstrlen + 8] # Not needed for validation now
        received_info_hash = received_handshake[1 + pstrlen + 8 : 1 + pstrlen + 8 + 20]
        peer_peer_id = received_handshake[1 + pstrlen + 8 + 20 : HANDSHAKE_LENGTH]

        #5 VALIDATE THE HANDSHAKE

        if pstr!=PROTOCOL_STRING:
            print(f"Handshake failed with {peer_addr}: Incorrect protocol string.")

            return None, None, None
        
        if received_info_hash != info_hash:
            print(f"Handshake failed with {peer_addr}: Info hash mismatch. Expected {info_hash.hex()}, got {received_info_hash.hex()}") 

            return None, None, None

        print(f"Handshake successful with {peer_addr}. Peer ID: {peer_peer_id.hex()}") 
    
        return reader, writer, peer_peer_id
    
    except ValueError as e:
        print(f"Error creating handshake for {peer_addr}: {e}") 
        return None, None, None
    
    except (asyncio.TimeoutError, ConnectionRefusedError, OSError) as e:
        print(f"Failed to connect or handshake with {peer_addr}: {e}") 
        return None, None, None
    
    except Exception as e:
        print(f"An unexpected error occurred with {peer_addr}: {e}") # Replaced logging.error
        return None, None, None
    
    finally:
        # Ensure the writer is closed if it was opened and not returned
        if writer and (reader is None or writer is None): # Only close if not successfully returned
            writer.close()


async def main_peer_conn_phase(peers:list,info_hash:bytes,my_peer_id:bytes):
    """
    Main function to initiate peer connections and handshakes concurrently.

    Args:
        peers (list): A list of (ip, port) tuples for peers from the tracker.
        info_hash (bytes): The 20-byte SHA1 hash of the torrent's info dictionary.
        my_peer_id (bytes): Your client's 20-byte peer ID.
    """

    print("Starting Phase 1, Part 1: Establishing Peer Connections and Handshaking.") 

    tasks=[]
    #CREATE CONNECTION FOR EACH PEER
    for ip,port in peers:
        #each task will try to perform handshake and return the connection objects or None
        tasks.append(perform_handshake(ip,port,info_hash,my_peer_id))


    # Run all connection tasks concurrently
    # asyncio.gather runs coroutines concurrently and waits for them all to complete.
    # The return value will be a list of results (reader, writer, peer_peer_id or None,None,None)
    results=await asyncio.gather(*tasks,return_exceptions=False)# return_exceptions=False for cleaner success/fail handling

    successful_connections=[]
    failed_connections=[]

    for i,(reader,writer,peer_peer_id) in enumerate(results):
        ip,port=peers[i] # Get original peer details for logging
        if reader and writer and peer_peer_id:
            successful_connections.append({
                'ip': ip,
                'port': port,
                'reader': reader,
                'writer': writer,
                'peer_peer_id': peer_peer_id
            })
        else:
            failed_connections.append((ip, port))

    print('================================================')
    
    print(f"Finished connection attempts. Successful: {len(successful_connections)}, Failed: {len(failed_connections)}") 

    print('==================================================')
    #some low-lvl clean up code##
    
    for conn_info in successful_connections:
        print(f"Connected and Handshaked with: {conn_info['ip']}:{conn_info['port']} (Peer ID: {conn_info['peer_peer_id'].hex()})") # Replaced logging.info
    # For failed connections, the warning messages are already printed by perform_handshake()

    ##some low-lvl clean up code##

    # Close writers for successful connections if not explicitly used later
    # In a real client, these would be managed by a higher-level peer management system.
    # For this example, we close them here to clean up.
    for conn_info in successful_connections:
        conn_info['writer'].close()
        await conn_info['writer'].wait_closed()