import sqlite3
from datetime import date
from pathlib import Path

from sqlalchemy import text
from app.db.session import engine
from app.core.logging import logger

NORTHWIND_DB_PATH = Path("/app/northwind.db")


def _load_from_northwind() -> tuple[list, list]:
    nw = sqlite3.connect(str(NORTHWIND_DB_PATH))
    try:
        cur = nw.cursor()
        cur.execute("""
            SELECT p.ProductID, p.ProductName, c.CategoryName, p.UnitPrice, p.UnitsInStock
            FROM Products p JOIN Categories c ON p.CategoryID = c.CategoryID
            ORDER BY p.ProductID
        """)
        products = cur.fetchall()

        cur.execute("""
            SELECT
                ROW_NUMBER() OVER (ORDER BY od.OrderID, od.ProductID),
                od.ProductID,
                od.Quantity,
                c.CompanyName,
                strftime('%Y-%m-%d', o.OrderDate),
                ROUND(od.UnitPrice * od.Quantity * (1 - od.Discount), 2)
            FROM "Order Details" od
            JOIN Orders o ON od.OrderID = o.OrderID
            JOIN Customers c ON o.CustomerID = c.CustomerID
            ORDER BY od.OrderID, od.ProductID
            LIMIT 500
        """)
        # Convert date strings to date objects for PostgreSQL
        orders = [
            (row[0], row[1], row[2], row[3], date.fromisoformat(row[4]), row[5])
            for row in cur.fetchall()
        ]
    finally:
        nw.close()
    return products, orders


async def seed_catalog_db() -> None:
    async with engine.begin() as conn:
        await conn.execute(text("""
            CREATE TABLE IF NOT EXISTS products (
                id      INTEGER PRIMARY KEY,
                name    TEXT    NOT NULL,
                category TEXT   NOT NULL,
                price   NUMERIC NOT NULL,
                stock   INTEGER NOT NULL
            )
        """))

        await conn.execute(text("""
            CREATE TABLE IF NOT EXISTS orders (
                id            INTEGER PRIMARY KEY,
                product_id    INTEGER NOT NULL REFERENCES products(id),
                quantity      INTEGER NOT NULL,
                customer_name TEXT    NOT NULL,
                order_date    DATE    NOT NULL,
                total_price   NUMERIC NOT NULL
            )
        """))

        result = await conn.execute(text("SELECT COUNT(*) FROM products"))
        count = result.scalar()
        if count == 0:
            products, orders = _load_from_northwind()
            await conn.execute(
                text("INSERT INTO products (id, name, category, price, stock) VALUES (:id, :name, :cat, :price, :stock)"),
                [{"id": r[0], "name": r[1], "cat": r[2], "price": r[3], "stock": r[4]} for r in products],
            )
            await conn.execute(
                text("INSERT INTO orders (id, product_id, quantity, customer_name, order_date, total_price) VALUES (:id, :pid, :qty, :cust, :date, :total)"),
                [{"id": r[0], "pid": r[1], "qty": r[2], "cust": r[3], "date": r[4], "total": r[5]} for r in orders],
            )
            logger.info("catalog_db_seeded", products=len(products), orders=len(orders))
        else:
            logger.info("catalog_db_already_seeded")
