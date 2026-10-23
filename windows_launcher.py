"""윈도우용 로컬 실행 도우미. 인증값은 프로세스 환경에만 보관합니다."""
import getpass
import os
import socket
import sys
import threading
import urllib.error
import urllib.request
import webbrowser


def main():
    try:
        from app.main import create_app
        import uvicorn
    except ModuleNotFoundError:
        print('실행에 필요한 패키지가 설치되지 않았습니다.')
        print('setup_windows.cmd를 다시 실행하고 Setup complete 메시지를 확인해 주세요.')
        print('설치 실패 시 해당 창의 오류 내용을 확인해 주세요. 인증값은 입력할 필요가 없습니다.')
        return 1
    print('안전보건 법령 모니터')
    print('1. 실제 법제처 API 사용')
    print('2. 인증 없이 가상 예시 화면 보기')
    choice = input('번호를 입력하세요 [1/2]: ').strip()
    if choice not in ('1', '2'):
        print('1 또는 2를 입력한 뒤 다시 실행해 주세요.')
        return 1
    demo = choice == '2'
    if not demo and not os.environ.get('LAW_OC', '').strip():
        # Do not echo, write, pass as an argument, or commit the credential.
        if not sys.stdin.isatty():
            print('인증값을 안전하게 입력할 수 있는 터미널에서 실행해 주세요.')
            return 1
        value = getpass.getpass('법제처 API 인증값을 입력하세요 (화면에 표시되지 않습니다): ').strip()
        if not value:
            print('인증값이 비어 있습니다.')
            return 1
        os.environ['LAW_OC'] = value
    app = create_app(demo=demo)
    # Retain the chosen socket until the server owns it, avoiding port races.
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        listener.bind(('127.0.0.1', 0))
        listener.listen(128)
        port = listener.getsockname()[1]
        address = f'http://127.0.0.1:{port}'
        def open_when_ready():
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
            for _ in range(60):
                if stop.wait(0.5):
                    return
                try:
                    with opener.open(address + '/api/health', timeout=1) as response:
                        if response.status == 200:
                            webbrowser.open(address)
                            return
                except (OSError, urllib.error.URLError):
                    continue
        stop = threading.Event()
        thread = threading.Thread(target=open_when_ready, daemon=True)
        thread.start()
        print('실행 중입니다. 브라우저가 자동으로 열립니다.')
        print('창을 닫거나 Ctrl+C를 누르면 서버가 종료됩니다.')
        print('실제 모드는 화면에서 ‘지금 확인’을 누르세요. API 승인과 호출 서버 등록 조건이 적용됩니다.')
        try:
            uvicorn.Server(uvicorn.Config(app, host='127.0.0.1', port=port)).run(sockets=[listener])
        finally:
            stop.set()
            thread.join(timeout=2)
    finally:
        listener.close()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
