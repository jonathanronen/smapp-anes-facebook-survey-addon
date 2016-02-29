"""
This script runs in the background next to the webapp. It periodically checks the "users" table in the database, and if a new user
had been added, downloads all of that user's data.

Usage:
    python background_crawler.py

@jonathanronen 2016/2
"""

import os
import yaml
import argparse
import facebook
import requests
import data_stores
from time import sleep
from bson import ObjectId
from datetime import datetime
from pymongo import MongoClient

from smappPy.smapp_logging import logging
logger = logging.getLogger(__name__)
logging.getLogger("requests").setLevel(logging.WARNING)

default_settings_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'smapp_facebook_signon', 'settings.yml')

def get_mongo_collection(server, port, username, password, dbname, colname):
    cl = MongoClient(server, port)
    db = cl[dbname]
    if username and password:
        db.authenticate(username, password)
    return db[colname]

def get_users_queue(db_host, db_port, db_username, db_password, db_name):
    col = get_mongo_collection(db_host, db_port, db_username, db_password, db_name, 'users')
    users = list(col.find({'downloaded': {'$exists': False}}))
    return users

def set_user_updated(db_host, db_port, db_username, db_password, db_name, user_id):
    col = get_mongo_collection(db_host, db_port, db_username, db_password, db_name, 'users')
    r = col.update_one({'_id': ObjectId(user_id)}, { '$set': {'downloaded': datetime.now()} } )

def download_data_for_user(user, data_store):
    logger.info("downloading data for user {} into data store.".format(user['user']['id']))
    g = facebook.GraphAPI(user['token']['access_token'])

    logger.info("Reading user metadata to determine fields to download")
    mymeta = g.get_object('me', metadata=1)
    fields = [f['name'] for f in mymeta['metadata']['fields']]
    nonbusiness_fields = [e for e in fields if 'business' not in e]
    
    logger.info("Downloading user public profile with all fields")
    profile = g.get_object('me', fields=','.join(nonbusiness_fields))
    data_store.store_object("{}.profile".format(user['user']['id']), profile)
    logger.info("Public profile saved to data store.")

    for graph_edge in mymeta['metadata']['connections']:
        if graph_edge in ['picture']:
            logger.info("Skipping graph edge {}".format(graph_edge))
            continue
        logger.info("Now downloading all data for the graph edge {}".format(graph_edge))
        alldata = download_with_paging(mymeta['metadata']['connections'][graph_edge])
        data_store.store_object("{}.{}".format(user['user']['id'], graph_edge), alldata)
        logger.info("{} saved to data store".format(graph_edge))
    logger.info("Gone through all fields and edges in user metadata. All stored.")
    
    return True

def download_with_paging(link):
    try:
        resp = requests.get(link).json()
        all_the_things = resp.get('data', [])
        while 'next' in resp.get('paging', {}) and len(resp.get('data',[])) > 0:
            resp = requests.get(resp['paging']['next']).json()
            all_the_things += resp.get('data', [])
        return all_the_things
    except ValueError:
        logger.info("Edge wasn't a json, storing raw content")
        return requests.get(link).content


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("-c", "--config-file", default=default_settings_path,
        help="Path to config file [smapp_facebok_signon/settings.yml]")
    parser.add_argument("-s", "--sleep-time", default=300, type=int,
        help="Time (in seconds) to wait before checking queue [300]")
    args = parser.parse_args()

    with open(args.config_file, 'rt') as infile:
        SETTINGS = yaml.load(infile)

    logger.info("Hi.")
    logger.info("Queue is at {server}:{port}/{db}/{col}".format(
        server=SETTINGS['database']['host'],
        port=SETTINGS['database']['port'],
        db=SETTINGS['database']['db'],
        col='users'
        ))
    
    logger.info("Data store is {}".format(
        SETTINGS['data_store']['store_class']))
    data_store = getattr(data_stores, SETTINGS['data_store']['store_class'])(**SETTINGS['data_store']['store_params'])

    users_queue = get_users_queue(
        SETTINGS['database']['host'],
        SETTINGS['database']['port'],
        SETTINGS['database']['username'],
        SETTINGS['database']['password'],
        SETTINGS['database']['db'],
        )

    while True:
        while len(users_queue) > 0:
            logger.info("Downloading data for {name} ({id})".format(
                name=users_queue[0]['user']['name'],
                id=users_queue[0]['user']['id']))
            u = users_queue.pop(0)
            if download_data_for_user(u, data_store):
                logger.info("Data stored succesfully.")
                set_user_updated(
                    SETTINGS['database']['host'],
                    SETTINGS['database']['port'],
                    SETTINGS['database']['username'],
                    SETTINGS['database']['password'],
                    SETTINGS['database']['db'],
                    u['_id']
                    )
                logger.info("User marked as downloaded in DB")
        while len(users_queue) == 0:
            logger.info("Sleeping for {} seconds before re-checking if there's work to do".format(args.sleep_time))
            sleep(args.sleep_time)
            users_queue = get_users_queue(
                SETTINGS['database']['host'],
                SETTINGS['database']['port'],
                SETTINGS['database']['username'],
                SETTINGS['database']['password'],
                SETTINGS['database']['db'],
                )

