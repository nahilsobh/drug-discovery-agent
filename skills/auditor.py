import requests

def check_trials(sponsor_keyword, disease_name):
    ct_url = "https://clinicaltrials.gov/api/v2/studies"
    params = {
        "query.cond": disease_name,
        "query.term": sponsor_keyword,
        "pageSize": 0 
    }
    try:
        r = requests.get(ct_url, params=params, timeout=10)
        return r.json().get('totalCount', 0)
    except:
        return 0
