import os
import time
import sys
from collections import OrderedDict

import pandas as pd
import praw
import prawcore
import spacy
from praw.models import Comment

from argmatcher import ArgMatcher
from local_info import USER_INFO
from response_templates import END_TEMPLATE, FAILURE_COMMENT, FAILURE_PM, GFORM_LINK

def read_list(file):
    completed = []
    with open(file) as fp:
        for line in fp:
            completed.append(line.strip())
    return completed

def load_myth_links(file):
    df = pd.read_csv(file, names=['argument', 'text', 'link'])
    return OrderedDict({k:v for k,v in zip(df['argument'].values, 
                                           df['link'].values) if v})

class MentionsBot:

    def __init__(self, argmatch, user_info, threshold=0.5):
        self.reddit = praw.Reddit(
                    check_for_async=False,
                    **user_info
                )
        self.inbox = praw.models.Inbox(self.reddit, _data={})
        self.argmatch = argmatch
        self.threshold = threshold
        self.blacklisted_subreddits = set(['suicidewatch', 'depression'])

        self.completed = []
        self.completed_file = './completed.csv'
        if os.path.isfile(self.completed_file):
            self.completed = read_list(self.completed_file)

        self.missed = []
        self.missed_file = './missed.csv'
        if os.path.isfile(self.missed_file):
            self.missed = read_list(self.missed_file)

        self.alphabet = 'abcdefghijklmnopqrstuvwxyz'

        self.arg_link_dict = load_myth_links('./knowledge/vegan_myths.csv')

        self.end_template = END_TEMPLATE
        self.failure_comment = FAILURE_COMMENT
        self.gform_link = GFORM_LINK
        self.failure_pm = FAILURE_PM

    def clear_already_replied(self):
        """
        Go through mentions manually to tick off if we have already replied
        """
        for mention in self.inbox.mentions(limit=None):
            if mention not in self.completed:
                if isinstance(mention, Comment):
                    parent = mention.parent()
                    if isinstance(parent, Comment):
                        parent.refresh()
                        replies = parent.replies.list()
                        reply_authors = [r.author for r in replies]
                        if 'animalsupportbot' in reply_authors:
                            self.completed.append(mention)
                            self.append_file(self.completed_file, mention)

    def append_file(self, file, comment_id):
        with open(file, 'a') as wp:
            line = '{}\n'.format(comment_id)
            wp.write(line)


    def format_response_persentence(self, resps):
        """
        Formatting responses given from the argument matcher
        """
        args = OrderedDict({})
        for r in resps:
            inp, sim, arg, passage = r
            if arg not in args:
                args[arg] = {'passage':passage, 'quotes':[inp], 'sim':sim}
            else:
                args[arg]['quotes'].append(inp)
                if args[arg]['sim'] < sim:
                    #replace the passage if this sentence is better matched
                    args[arg]['sim'] = sim
                    args[arg]['passage'] = passage
        
        parts = []
        arglist = []
        
        for i, arg in enumerate(args):
            quotes = ''.join(['>{} \n\n'.format(q) for q in args[arg]['quotes']])
            passage = args[arg]['passage'] + '^(({})^)'.format(self.alphabet[i]) + '\n'
            parts.append(quotes)
            parts.append(passage)
            if arg in self.arg_link_dict:
                arglist.append('[({}): {}]({})'.format(self.alphabet[i], arg, self.arg_link_dict[arg]))
            else:
                arglist.append('({}): {}'.format(self.alphabet[i], arg))
        
        parts.append(self.end_template.format(', '.join(arglist)))
        return '\n'.join(parts)
        

    def reply_mentions_persentence(self, limit=None):
        """
        Main functionality. Go through mentions and reply to parent comments
        Uses persentence argmatcher
        """
        for mention in self.inbox.mentions(limit=limit):
            # Temporary restriction on only replying in test subreddit
            if mention.subreddit.display_name.lower() != 'testanimalsupportbot':
                continue

            # Skip mention if included in blacklisted subreddits
            if mention.subreddit.display_name.lower() in self.blacklisted_subreddits:
                self.completed.append(mention)
                self.append_file(self.completed_file, mention)
                continue

            # Proceed if mention has not been dealt with
            if mention not in self.completed and mention not in self.missed:
                if isinstance(mention, Comment):
                    parent = mention.parent()
                    if isinstance(parent, Comment):

                        # Check if parent has been handled (in case of multiple mentions)
                        if parent in self.completed or parent in self.missed:
                            self.completed.append(mention)
                            self.append_file(self.completed_file, mention)
                            continue

                        comment_text = parent.body
                        resps = self.argmatch.match_text_persentence(comment_text, threshold=self.threshold)

                        if resps:
                            formatted_response = self.format_response_persentence(resps)
                            parent.reply(formatted_response)
                            print(formatted_response)

                            # Add both the mention and the parent to the completed list
                            self.completed.append(mention)
                            self.append_file(self.completed_file, mention)
                            self.completed.append(parent)
                            self.append_file(self.completed_file, parent)
                        else:
                            mention.reply(self.failure_comment)
                            mention.author.message("We couldn't find a response to the comment!",
                                                   self.failure_pm.format(self.argmatch.prefilter(parent.body), self.gform_link))

                            # Add both the mention and the parent to the completed list
                            self.missed.append(mention)
                            self.append_file(self.missed_file, mention)
                            self.missed.append(parent)
                            self.append_file(self.missed_file, parent)
    
    def run(self, refresh_rate=600, timeout_retry=600):
        self.clear_already_replied()
        while True:
            try:
                self.reply_mentions_persentence()
                print('{}\tReplied to mentions, sleeping for {} seconds...'.format(time.ctime(), refresh_rate))
                time.sleep(refresh_rate)
            except prawcore.exceptions.ServerError:
                print('Got a ServerError, sleeping for {} seconds before trying again...'.format(timeout_retry))
                time.sleep(timeout_retry)


if __name__ == "__main__":
    refresh_rate = int(sys.argv[1])
    threshold = float(sys.argv[2])
    nlp = spacy.load('en_core_web_lg')
    nlp.add_pipe('universal_sentence_encoder', config={'model_name':'en_use_lg'})

    argm = ArgMatcher(nlp, None, None, preload=True)
    mb = MentionsBot(argm, USER_INFO, threshold=threshold)

    mb.run(refresh_rate=refresh_rate)


