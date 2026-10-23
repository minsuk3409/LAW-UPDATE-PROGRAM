import argparse
import uvicorn
from .main import create_app


def main():
    parser = argparse.ArgumentParser(description='안전보건 법령 모니터 웹 화면')
    parser.add_argument('--demo', action='store_true', help='별도 DB에 가상 예시 자료 표시')
    parser.add_argument('--db', help='SQLite 저장 경로')
    parser.add_argument('--host', default='127.0.0.1')
    parser.add_argument('--port', type=int, default=8000)
    args = parser.parse_args()
    uvicorn.run(create_app(args.db, demo=args.demo), host=args.host, port=args.port)


if __name__ == '__main__':
    main()
