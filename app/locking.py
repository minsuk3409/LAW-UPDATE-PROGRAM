"""Windows와 POSIX에서 같은 DB의 중복 수집을 방지하는 파일 잠금."""
import errno
import os

if os.name == 'nt':
    import msvcrt
else:
    import fcntl


class BusyError(Exception):
    pass


def acquire(path):
    handle = open(path, 'a+b')
    try:
        if os.name == 'nt':
            # msvcrt locks a byte range from the current file position.
            handle.seek(0, os.SEEK_END)
            if handle.tell() == 0:
                handle.write(b'\0')
                handle.flush()
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError as exc:
        handle.close()
        if exc.errno in (errno.EACCES, errno.EAGAIN, errno.EDEADLK):
            raise BusyError('이미 조회가 진행 중입니다.') from None
        raise
    return handle
