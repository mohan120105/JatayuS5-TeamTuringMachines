#!/usr/bin/env python3
"""Simulate a conversational agent that reads Jira and returns related tickets.

Usage:
  python simulate_jira_agent.py --query "KYC onboarding"

If run without --query it enters a simple REPL where you can type questions.
"""
import os
import argparse
from create_jira_issue import get_config

def connect_jira():
    cfg = get_config()
    server = cfg.get('server')
    user = cfg.get('user')
    token = cfg.get('token')
    if not server or not user or not token:
        raise RuntimeError('Missing Jira config; set env or .env (JIRA_SERVER, ATLASSIAN_USER_EMAIL, ATLASSIAN_API_TOKEN)')
    from jira import JIRA
    return JIRA(server=server, basic_auth=(user, token)), server

def build_jql_for_query(q: str):
    # naive mapping: search labels, summary, description
    q_esc = q.replace('"', '')
    clauses = []
    # search by label token
    for token in q_esc.split():
        token = token.strip()
        if not token:
            continue
        clauses.append(f'label = "{token}"')
    # also search summary/description fuzzy
    clauses.append(f'(summary ~ "{q_esc}" OR description ~ "{q_esc}")')
    jql = ' OR '.join(clauses)
    # restrict to project REQ by default
    jql = f'project = REQ AND ({jql})'
    return jql

def format_issue(issue, server):
    key = issue.key
    summary = getattr(issue.fields, 'summary', '')
    status = getattr(issue.fields, 'status', None)
    status_name = status.name if status else 'UNKNOWN'
    priority = getattr(issue.fields, 'priority', None)
    priority_name = priority.name if priority else 'None'
    labels = getattr(issue.fields, 'labels', [])
    url = f"{server.rstrip('/')}" + f"/browse/{key}"
    return f"- {key}: {summary} (status={status_name}, priority={priority_name}, labels={labels})\n  {url}"

def run_query_once(query):
    jira, server = connect_jira()
    jql = build_jql_for_query(query)
    print('Searching Jira with JQL:', jql)
    issues = jira.search_issues(jql, maxResults=20)
    if not issues:
        print('No matching Jira tickets found for query:', query)
        return
    print(f'Found {len(issues)} matching tickets:')
    for issue in issues:
        print(format_issue(issue, server))

def repl():
    print('Enter queries mentioning Jira or ticketing (type quit to exit).')
    while True:
        try:
            q = input('> ').strip()
        except EOFError:
            break
        if not q:
            continue
        if q.lower() in ('quit', 'exit'):
            break
        run_query_once(q)

def main():
    p = argparse.ArgumentParser()
    p.add_argument('--query', default=None)
    args = p.parse_args()
    if args.query:
        run_query_once(args.query)
    else:
        repl()

if __name__ == '__main__':
    main()
