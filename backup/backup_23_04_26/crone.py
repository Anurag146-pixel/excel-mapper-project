import time
import pymysql
from datetime import datetime
import sys
import os

# 🔥 Ensure same folder import works
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from ext import extract


def fetch_pending_records():
    connection = pymysql.connect(
        host="localhost",
        user="root",
        password="root",
        database="excel_reader",
        cursorclass=pymysql.cursors.DictCursor
    )

    try:
        with connection.cursor() as cursor:
            query = "SELECT * FROM token_details WHERE status = 'pending'"
            cursor.execute(query)
            results = cursor.fetchall()

            print(f"\n[{datetime.now()}] Pending Records:")

            for row in results:
                print(row)
                extract(row)   # 🔥 FULL ENGINE CALL

    except Exception as e:
        print("❌ Cron DB Error:", e)

    finally:
        connection.close()


# 🔁 Run every 30 seconds
while True:
    fetch_pending_records()
    time.sleep(30)