#!/usr/bin/env python
# -*- coding: utf-8 -*-
import os, sys
import twitter
import json
from prettytable import PrettyTable
from collections import Counter
from functools import partial
from sys import maxint

def oauth_login():
    
    auth = twitter.oauth.OAuth(OAUTH_TOKEN, OAUTH_TOKEN_SECRET,
                               CONSUMER_KEY, CONSUMER_SECRET)
    
    twitter_api = twitter.Twitter(auth=auth)
    return twitter_api

def twitter_search(twitter_api, q, max_results=200, **kw):
  
    search_results = twitter_api.search.tweets(q=q, count=100, **kw)
    
    statuses = search_results['statuses']
    
    max_results = min(1000, max_results)
    
    for _ in range(10): 
        try:
            next_results = search_results['search_metadata']['next_results']
        except KeyError, e: 
            break

        kwargs = dict([ kv.split('=') 
                        for kv in next_results[1:].split("&") ])
        
        search_results = twitter_api.search.tweets(**kwargs)
        statuses += search_results['statuses']
        
        if len(statuses) > max_results: 
            break
            
    return statuses

def extract_tweet_entities(statuses):

    if len(statuses) == 0:
        return [], [], [], [], []
    
    screen_names = [ user_mention['screen_name'] 
                         for status in statuses
                            for user_mention in status['entities']['user_mentions'] ]
    
    hashtags = [ hashtag['text'] 
                     for status in statuses 
                        for hashtag in status['entities']['hashtags'] ]

    urls = [ url['expanded_url'] 
                     for status in statuses 
                        for url in status['entities']['urls'] ]
    
    symbols = [ symbol['text']
                   for status in statuses
                       for symbol in status['entities']['symbols'] ]
               
    if status['entities'].has_key('media'): 
        media = [ media['url'] 
                         for status in statuses  
                            for media in status['entities']['media'] ]
    else:
        media = []

    return screen_names, hashtags, urls, media, symbols


def get_common_tweet_entities(statuses, entity_threshold=3):

    tweet_entities = [  e
                        for status in statuses
                            for entity_type in extract_tweet_entities([status]) 
                                for e in entity_type 
                     ]

    c = Counter(tweet_entities).most_common()

    return [ (k,v) 
             for (k,v) in c
                 if v >= entity_threshold
           ]

def get_user_profile(twitter_api, screen_names=None, user_ids=None):
   
    assert (screen_names != None) != (user_ids != None), \
    "Must have screen_names or user_ids, but not both"
    
    items_to_info = {}

    items = screen_names or user_ids
    
    while len(items) > 0:

        items_str = ','.join([str(item) for item in items[:100]])
        items = items[100:]

        if screen_names:
            response = make_twitter_request(twitter_api.users.lookup, 
                                            screen_name=items_str)
        else: 
            response = make_twitter_request(twitter_api.users.lookup, 
                                            user_id=items_str)
    
        for user_info in response:
            if screen_names:
                items_to_info[user_info['screen_name']] = user_info
            else: 
                items_to_info[user_info['id']] = user_info

    return items_to_info

def make_twitter_request(twitter_api_func, max_errors=10, *args, **kw): 
    
    def handle_twitter_http_error(e, wait_period=2, sleep_when_rate_limited=True):
    
        if wait_period > 3600: 
            print >> sys.stderr, 'Too many retries. Quitting.'
            raise e
    
        if e.e.code == 401:
            print >> sys.stderr, 'Encountered 401 Error (Not Authorized)'
            return None
        elif e.e.code == 404:
            print >> sys.stderr, 'Encountered 404 Error (Not Found)'
            return None
        elif e.e.code == 429: 
            print >> sys.stderr, 'Encountered 429 Error (Rate Limit Exceeded)'
            if sleep_when_rate_limited:
                print >> sys.stderr, "Retrying in 15 minutes...ZzZ..."
                sys.stderr.flush()
                time.sleep(60*15 + 5)
                print >> sys.stderr, '...ZzZ...Awake now and trying again.'
                return 2
            else:
                raise e 
        elif e.e.code in (500, 502, 503, 504):
            print >> sys.stderr, 'Encountered %i Error. Retrying in %i seconds' % \
                (e.e.code, wait_period)
            time.sleep(wait_period)
            wait_period *= 1.5
            return wait_period
        else:
            raise e

    # End of nested helper function
    
    wait_period = 2 
    error_count = 0 

    while True:
        try:
            return twitter_api_func(*args, **kw)
        except twitter.api.TwitterHTTPError, e:
            error_count = 0 
            wait_period = handle_twitter_http_error(e, wait_period)
            if wait_period is None:
                return
        except URLError, e:
            error_count += 1
            time.sleep(wait_period)
            wait_period *= 1.5
            print >> sys.stderr, "URLError encountered. Continuing."
            if error_count > max_errors:
                print >> sys.stderr, "Too many consecutive errors...bailing out."
                raise
        except BadStatusLine, e:
            error_count += 1
            time.sleep(wait_period)
            wait_period *= 1.5
            print >> sys.stderr, "BadStatusLine encountered. Continuing."
            if error_count > max_errors:
                print >> sys.stderr, "Too many consecutive errors...bailing out."
                raise

def get_friends_followers_ids(twitter_api, screen_name=None, user_id=None,
                              friends_limit=maxint, followers_limit=maxint):
    
    assert (screen_name != None) != (user_id != None), \
    "Must have screen_name or user_id, but not both"
    
    get_friends_ids = partial(make_twitter_request, twitter_api.friends.ids, 
                              count=5000)
    get_followers_ids = partial(make_twitter_request, twitter_api.followers.ids, 
                                count=5000)

    friends_ids, followers_ids = [], []
    
    for twitter_api_func, limit, ids, label in [
                    [get_friends_ids, friends_limit, friends_ids, "friends"], 
                    [get_followers_ids, followers_limit, followers_ids, "followers"]
                ]:
        
        if limit == 0: continue
        
        cursor = -1
        while cursor != 0:
        
            if screen_name: 
                response = twitter_api_func(screen_name=screen_name, cursor=cursor)
            else:
                response = twitter_api_func(user_id=user_id, cursor=cursor)

            if response is not None:
                ids += response['ids']
                cursor = response['next_cursor']

        
            if len(ids) >= limit or response is None:
                break

    return friends_ids[:friends_limit], followers_ids[:followers_limit]


def setwise_friends_followers_analysis(screen_name, friends_ids, followers_ids):
    
    friends_ids, followers_ids = set(friends_ids), set(followers_ids)
    
    print '{0} {1} жиргээч дагаж байна'.format(screen_name, len(friends_ids))

    print '{0} {1} дагагчтай'.format(screen_name, len(followers_ids))
    
    print '{2}-н дагаж буй {1} жиргээчээс {0} нь буцаан дагаагүй байна'.format(
            len(friends_ids.difference(followers_ids)), 
            len(friends_ids), screen_name)
    
    print '{2} {1} дагагчдынхаа {0}-г дагаагүй байна'.format(
            len(followers_ids.difference(friends_ids)), 
            len(followers_ids), screen_name)
    
    print '{0}-н дагагч/дагуулагчдын давхцал {1}'.format(
            screen_name, len(friends_ids.intersection(followers_ids)))

screen_name = "enqush"

twitter_api = oauth_login()

friends_ids, followers_ids = get_friends_followers_ids(twitter_api, 
                                                       screen_name=screen_name)
setwise_friends_followers_analysis(screen_name, friends_ids, followers_ids)






 

