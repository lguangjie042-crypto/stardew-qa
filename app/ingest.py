from .db import init_db
from .wiki import sync_official_wiki


def main() -> None:
    init_db()
    result = sync_official_wiki()
    print(f"已从官方 Wiki 同步 {result['fetched']} 页，库内共 {result['total']} 条。来源：{result['source']}")


if __name__ == "__main__":
    main()
