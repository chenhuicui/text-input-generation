from __future__ import annotations

from pathlib import Path
import base64
import pymysql


PROJECT_ROOT = Path("/Users/cuichenhui/Documents/local-repositories/llm-empirical-study-workspace/llm-empirical-study")

SCREENSHOT_DIR = PROJECT_ROOT / "output" / "screenshots"
XML_DIR = PROJECT_ROOT / "output" / "xml_dumps"


def read_text_file(path: Path) -> str | None:
    if not path.exists():
        print(f"[WARN] XML not found: {path}")
        return None
    return path.read_text(encoding="utf-8", errors="replace")


def read_image_as_base64(path: Path) -> str | None:
    if not path.exists():
        print(f"[WARN] Screenshot not found: {path}")
        return None

    with path.open("rb") as f:
        return base64.b64encode(f.read()).decode("utf-8")


def update_xml_and_image64_for_valid_prompt_context(
    host: str,
    port: int,
    user: str,
    password: str,
    database: str = "tosem_text_input_db",
) -> None:
    """
    Update xml and image_64 fields in t_valid_prompt_context.

    Expected files:
      output/xml_dumps/hierarchy_<app_id>.xml
      output/screenshots/screenshot_<app_id>.png
    """

    conn = pymysql.connect(
        host=host,
        port=port,
        user=user,
        password=password,
        database=database,
        charset="utf8mb4",
        autocommit=False,
    )

    try:
        with conn.cursor() as cursor:
            cursor.execute("SELECT app_id FROM t_valid_prompt_context")
            app_ids = [row[0] for row in cursor.fetchall()]

            print(f"[INFO] Found {len(app_ids)} app_ids in t_valid_prompt_context")

            updated = 0
            missing_xml = 0
            missing_img = 0

            for app_id in app_ids:
                xml_path = XML_DIR / f"hierarchy_{app_id}.xml"
                img_path = SCREENSHOT_DIR / f"screenshot_{app_id}.png"

                xml_content = read_text_file(xml_path)
                image_64 = read_image_as_base64(img_path)

                if xml_content is None:
                    missing_xml += 1
                if image_64 is None:
                    missing_img += 1

                cursor.execute(
                    """
                    UPDATE t_valid_prompt_context
                    SET
                        xml = COALESCE(%s, xml),
                        image_64 = COALESCE(%s, image_64)
                    WHERE app_id = %s
                    """,
                    (xml_content, image_64, app_id),
                )

                updated += cursor.rowcount

            conn.commit()

            print(f"[DONE] Updated rows: {updated}")
            print(f"[INFO] Missing XML files: {missing_xml}")
            print(f"[INFO] Missing screenshot files: {missing_img}")

    except Exception:
        conn.rollback()
        raise

    finally:
        conn.close()

def verify_xml_and_image64(

    host: str,

    port: int,

    user: str,

    password: str,

    database: str = "tosem_text_input_db",

) -> None:

    conn = pymysql.connect(

        host=host,

        port=port,

        user=user,

        password=password,

        database=database,

        charset="utf8mb4",

    )

    bad = []

    try:

        with conn.cursor() as cursor:

            cursor.execute("""

                SELECT app_id, xml, image_64

                FROM t_valid_prompt_context

            """)

            rows = cursor.fetchall()

            for app_id, db_xml, db_image64 in rows:

                xml_path = XML_DIR / f"hierarchy_{app_id}.xml"

                img_path = SCREENSHOT_DIR / f"screenshot_{app_id}.png"

                local_xml = xml_path.read_text(encoding="utf-8", errors="replace")

                local_img_bytes = img_path.read_bytes()

                local_image64 = base64.b64encode(local_img_bytes).decode("utf-8")

                issues = []

                if db_xml != local_xml:

                    issues.append(

                        f"xml mismatch: db_chars={len(db_xml or '')}, local_chars={len(local_xml)}"

                    )

                if db_image64 != local_image64:

                    issues.append(

                        f"image64 mismatch: db_len={len(db_image64 or '')}, local_len={len(local_image64)}"

                    )

                try:

                    restored = base64.b64decode(db_image64 or "", validate=True)

                    if restored != local_img_bytes:

                        issues.append(

                            f"decoded image mismatch: decoded_bytes={len(restored)}, local_bytes={len(local_img_bytes)}"

                        )

                except Exception as e:

                    issues.append(f"base64 decode failed: {e}")

                if issues:

                    bad.append((app_id, issues))

        if not bad:

            print(f"[OK] All {len(rows)} rows match local XML and screenshot files.")

        else:

            print(f"[WARN] Found {len(bad)} mismatched rows.")

            for app_id, issues in bad[:20]:

                print(f"- {app_id}")

                for issue in issues:

                    print(f"  * {issue}")

    finally:

        conn.close()

if __name__ == "__main__":
    verify_xml_and_image64(
        host="101.200.47.200",
        port=3306,
        user="must_user",
        password="REDACTED_USE_ENV_MYSQL_PASSWORD",
        database="tosem_text_input_db",
    )