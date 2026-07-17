import os
from pymongo import MongoClient

MONGO_URI = os.getenv("MONGO_URI", "mongodb://127.0.0.1:27017/")
DB_NAME = "Damian"

ORDERS_DEMO = [
    {"Order Type": "Buy", "Profit": "300$", "Balance": "130,620"},
    {"Order Type": "Buy", "Profit": "200$", "Balance": "131,220"},
    {"Order Type": "Buy", "Profit": "500$", "Balance": "131,220"},
    {"Order Type": "Sell", "Profit": "-100$", "Balance": "101,120"},
    {"Order Type": "Buy", "Profit": "400$", "Balance": "101,520"},
    {"Order Type": "Buy", "Profit": "100$", "Balance": "101,620"},
    {"Order Type": "Buy", "Profit": "300$", "Balance": "130,420"},
    {"Order Type": "Buy", "Profit": "300$", "Balance": "130,820"},
    {"Order Type": "Sell", "Profit": "-50$", "Balance": "100,720"},
    {"Order Type": "Buy", "Profit": "500$", "Balance": "131,220"},
    {"Order Type": "Sell", "Profit": "-100$", "Balance": "101,120"},
    {"Order Type": "Sell", "Profit": "-200$", "Balance": "101,020"},
    {"Order Type": "Buy", "Profit": "100$", "Balance": "101,420"},
    {"Order Type": "Buy", "Profit": "300$", "Balance": "101,520"},
    {"Order Type": "Sell", "Profit": "-50$", "Balance": "100,720"},
    {"Order Type": "Buy", "Profit": "400$", "Balance": "101,520"},
    {"Order Type": "Buy", "Profit": "500$", "Balance": "131,220"},
    {"Order Type": "Buy", "Profit": "100$", "Balance": "101,620"},
    {"Order Type": "Sell", "Profit": "-100$", "Balance": "101,120"},
    {"Order Type": "Buy", "Profit": "300$", "Balance": "101,520"},
]

ACCOUNTS_DEMO = [
    {"User Name": "Nicolle Pelayo", "Accounts": "*****363", "Total": "62,985"},
    {"User Name": "Enrique Pelayo", "Accounts": "*****282", "Total": "62,985"},
    {"User Name": "Roger Pelayo", "Accounts": "*****171", "Total": "61,470"},
    {"User Name": "Maria Chanot", "Accounts": "*****939", "Total": "62,985"},
    {"User Name": "Daniela Echeverri", "Accounts": "*****626", "Total": "62,985"},
]


def seed_demo_data():
    client = MongoClient(MONGO_URI, serverSelectionTimeoutMS=3000)
    try:
        db = client[DB_NAME]
        orders_col = db["BotBalance"]
        accounts_col = db["Accounts"]

        orders_col.delete_many({})
        accounts_col.delete_many({})

        if orders_col.count_documents({}) == 0:
            orders_col.insert_many(ORDERS_DEMO)
        if accounts_col.count_documents({}) == 0:
            accounts_col.insert_many(ACCOUNTS_DEMO)

        print(f"MongoDB seeded successfully in database '{DB_NAME}'")
        print(f"BotBalance documents: {orders_col.count_documents({})}")
        print(f"Accounts documents: {accounts_col.count_documents({})}")
    finally:
        client.close()


if __name__ == "__main__":
    seed_demo_data()
