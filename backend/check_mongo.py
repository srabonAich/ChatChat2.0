"""Simple connectivity check for MongoDB used by the backend.
Run this after starting MongoDB to verify Motor can connect.
"""
import os
import asyncio
import motor.motor_asyncio

async def main():
    mongo_url = os.environ.get('MONGODB_URI', 'mongodb://localhost:27017')
    print('Using MONGODB_URI=', mongo_url)
    client = motor.motor_asyncio.AsyncIOMotorClient(mongo_url)
    try:
        # list databases as a quick probe
        dbs = await client.list_database_names()
        print('Connected to MongoDB, databases:', dbs)
    except Exception as e:
        print('Connection failed:', e)
    finally:
        client.close()

if __name__ == '__main__':
    asyncio.run(main())
