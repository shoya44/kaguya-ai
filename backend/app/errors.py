class ChatError(Exception):
    def __init__(self, code: str, message: str, retry_after: int | None = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.retry_after = retry_after

    def event(self, turn_id: str | None = None) -> dict:
        return {'type': 'chat.error', 'turn_id': turn_id, 'code': self.code,
                'message': self.message, 'retry_after': self.retry_after}
