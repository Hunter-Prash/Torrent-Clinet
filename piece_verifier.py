import hashlib
import os

def verify(file_path, info):
    piece_length = info['piece length']
    pieces_blob = info['pieces']
    num_pieces = len(pieces_blob) // 20
    expected_hashes = [pieces_blob[i*20:(i+1)*20] for i in range(num_pieces)]

    with open(file_path, 'rb') as f:
        for i, expected in enumerate(expected_hashes):
            piece = f.read(piece_length)
            if not piece:
                print(f"❌ File too short for piece {i}")
                return
            actual_hash = hashlib.sha1(piece).digest()
            if actual_hash != expected:
                print(f"❌ Piece {i} hash mismatch")
                return
    print("✅ All pieces verified successfully.")

def verify_multifile(root_folder, info):
    piece_length = info['piece length']
    pieces_blob = info['pieces']
    num_pieces = len(pieces_blob) // 20
    expected_hashes = [pieces_blob[i*20:(i+1)*20] for i in range(num_pieces)]

    # Build a list of (full_path, length) for all files in order
    files = []
    for f in info['files']:
        rel_path = os.path.join(*f['path'])
        full_path = os.path.join(root_folder, rel_path)
        files.append((full_path, f['length']))
    
    # Open each file in order and read all data into a buffer
    data = b''
    for path, length in files:
        with open(path, 'rb') as file:
            data += file.read()

    # Verify each piece
    for i, expected in enumerate(expected_hashes):
        piece = data[i*piece_length:(i+1)*piece_length]
        if not piece:
            print(f"❌ Not enough data for piece {i}")
            return
        actual_hash = hashlib.sha1(piece).digest()
        if actual_hash != expected:
            print(f"❌ Piece {i} hash mismatch")
            return
    print("✅ All pieces verified successfully.")