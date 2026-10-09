import json

TYPES = {'start', 'stop', 'otp', 'logout', 'shutdown', 'configure', 'preview',
         'status', 'otp_required', 'authenticated', 'auth_status', 'error', 'stopped', 'comment_status', 'ready','session_invalid',
         'post_comment','post_result','post_ready','control','metadata','position','recording','completed','job_ready','player_closed'}
MAX_BYTES = 65536


def decode_message(line: str) -> dict:
    if len(line.encode('utf-8')) > MAX_BYTES:
        raise ValueError('Message too large')
    data = json.loads(line)
    if not isinstance(data, dict) or data.get('type') not in TYPES:
        raise ValueError('Invalid message')
    if not isinstance(data.get('request_id'), str) or len(data['request_id']) > 100:
        raise ValueError('Invalid request ID')
    return data
