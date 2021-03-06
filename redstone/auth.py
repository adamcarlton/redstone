# Copyright 2019 Mathew Odden <mathewrodden@gmail.com>
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

from __future__ import print_function

import base64
import json
import logging
import os
import pprint
import sys
import time
import threading

try:
    from urllib.parse import urlparse, urlencode, urlunsplit
except ImportError:
    from urlparse import urlparse, urlunsplit
    from urllib import urlencode

try:
    import http.client as httplib
except ImportError:
    import httplib


LOG = logging.getLogger(__name__)


class TokenManager(object):

    def __init__(self, api_key, iam_endpoint=None):
        self.api_key = api_key
        self.iam_endpoint = iam_endpoint
        self._token_info = {}
        self._lock = threading.RLock()

    def get_token(self):
        with self._lock:
            if (not self._token_info.get('access_token') or
                    self.is_refresh_token_expired()):
                self._request_token()
            elif self.is_token_expired():
                self._refresh_token()

            return self._token_info.get('access_token')

    def _request_token(self):
        token_resp = auth(apikey=self.api_key, iam_endpoint=self.iam_endpoint)
        if isinstance(token_resp, dict):
            self._token_info = token_resp
        else:
            raise Exception("Error getting token: %s" % token_resp)

    def _refresh_token(self):
        token_resp = auth(
            refresh_token=self._token_info.get('refresh_token'),
            iam_endpoint=self.iam_endpoint)
        if isinstance(token_resp, dict):
            self._token_info = token_resp
        else:
            raise Exception("Error getting refreshing token: %s" % token_resp)

    def is_token_expired(self):
        # refresh even with 20% time still remainig,
        # this should be 12 minutes before expiration for 1h tokens
        token_expire_time = self._token_info.get('expiration', 0)
        token_expires_in = self._token_info.get('expires_in', 0)
        return (time.time() >= (token_expire_time - (0.2 * token_expires_in)))

    def is_refresh_token_expired(self):
        # no idea how long these last,
        # but some other code suggested up to 30 days,
        # but it was also assuming they expire within 7 days...
        # assume 7 days, because better safe than sorry
        day = 24 * 60 * 60
        refresh_expire_time = self._token_info.get('expiration', 0) + (7 * day)
        return (time.time() >= refresh_expire_time)


def request(method, url, body=None, data=None, headers=None):
    parts = urlparse(url)

    if parts.scheme == 'https':
        conn = httplib.HTTPSConnection(parts.netloc)
    else:
        conn = httplib.HTTPConnection(parts.netloc)

    headers = headers if headers else {}

    if data:
        headers['Content-Type'] = 'application/x-www-form-urlencoded'
        body = urlencode(data)

    path = urlunsplit(
        ('', '', parts.path, parts.query, parts.fragment))

    LOG.debug(get_curl(method, url, headers))

    LOG.info('httplib %s %s' % (method, path))
    LOG.debug('headers=%s' % pprint.pformat(headers))
    LOG.debug('body=%r' % body)
    conn.request(method, path, body=body, headers=headers)
    resp = conn.getresponse()

    LOG.info('httplib response - %s %s' % (resp.status, resp.reason))

    return resp


def get_curl(method, url, headers):
    header_strs = []
    for k, v in headers.items():
        header_strs.append('-H "%s: %s"' % (k, v))

    header_str = ' '.join(header_strs)

    curl_str = 'curl -v -X%(method)s %(headers)s "%(url)s"' % {
        'method': method,
        'headers': header_str,
        'url': url
    }

    return curl_str


def auth(username=None, password=None, apikey=None,
         refresh_token=None, iam_endpoint=None):
    """
    Makes a authentication request to the IAM api
    :param username: Username
    :param password: Password
    :param apikey: IBMCloud/Bluemix API Key
    :param refresh_token: IBM IAM Refresh Token,
        if specified the refresh token is used to authenticate,
        instead of the API key
    :param iam_endpoint: base URL that can be specified
        to override the default IAM endpoint, if one, for example,
        wanted to test against their own IAM or an internal server
    :return: Response
    """
    if not iam_endpoint:
        iam_endpoint = 'https://iam.cloud.ibm.com/'

    if iam_endpoint[-1] == '/':
        iam_endpoint = iam_endpoint[:-1]

    api_endpoint = iam_endpoint + '/oidc/token'

    # HTTP Headers
    headers = {
        'Authorization': 'Basic Yng6Yng=',
        'Content-Type': 'application/x-www-form-urlencoded',
        'Accept': 'application/json'
    }

    # HTTP Payload
    data = {
        'response_type': 'cloud_iam',
        'uaa_client_id': 'cf',
        'uaa_client_secret': ''
    }

    # Setup grant type
    if apikey:
        data['grant_type'] = 'urn:ibm:params:oauth:grant-type:apikey'
        data['apikey'] = apikey
    elif refresh_token:
        data['grant_type'] = 'refresh_token'
        data['refresh_token'] = refresh_token
    elif username and password:
        data['grant_type'] = 'password'
        data['username'] = username
        data['password'] = password
    else:
        raise ValueError(
            "Must specify one of username/password, apikey, or refresh_token!")

    encoded = urlencode(data)

    resp = request('POST', api_endpoint, body=encoded, headers=headers)

    if resp.status == 200:
        jsonable = json.loads(resp.read().decode('utf-8'))
        return jsonable

    return resp.read()


def get_orgs(bearer_token):
    api_endpoint = 'https://api.ng.bluemix.net/v2/organizations'

    headers = {
        'Content-Type': 'application/x-www-form-urlencoded;charset=utf',
        'Authorization': 'Bearer %s' % bearer_token,
        'Accept': 'application/json;charset=utf-8'
    }

    resp = request('GET', api_endpoint, headers=headers)
    return resp.read()


def get_spaces(bearer_token, spaces_path):
    api_endpoint = 'https://api.ng.bluemix.net%s' % spaces_path

    headers = {
        'Content-Type': 'application/x-www-form-urlencoded;charset=utf',
        'Authorization': 'Bearer %s' % bearer_token,
        'Accept': 'application/json;charset=utf-8'
    }

    resp = request('GET', api_endpoint, headers=headers)
    return resp.read()


def find_space_and_org(bearer_token, org_name, space_name):
    org_resp = get_orgs(bearer_token)
    org_data = json.loads(org_resp)

    for org in org_data['resources']:
        if org_name == org.get('entity', {}).get('name'):
            org_info = org
            break

    space_resp = get_spaces(bearer_token, org_info['entity']['spaces_url'])
    space_data = json.loads(space_resp)

    for space in space_data['resources']:
        if space_name == space.get('entity', {}).get('name'):
            space_info = space
            break

    return org_info, space_info


def inspect_token(token):
    parts = token.split(".")[:2]
    decoded_parts = []
    for part in parts:
        padding = '=' * (len(part) % 4)
        part = str(part)
        decoded_part = base64.urlsafe_b64decode(part + padding)
        try:
            decoded_part = json.loads(decoded_part)
        except ValueError:
            pass
        decoded_parts.append(decoded_part)

    return decoded_parts


def main():
    api_key = None

    # iterate through possible things that could be used to get us the key,
    # last one that is not None or empty will win
    possible_keys = [os.environ.get('BLUEMIX_API_KEY'),
                     os.environ.get('IBMCLOUD_API_KEY')]
    for pkey in possible_keys:
        if pkey:
            api_key = pkey

    if not api_key:
        print("error: please set BLUEMIX_API_KEY or IBMCLOUD_API_KEY",
              file=sys.stderr)
        return 1

    tokman = TokenManager(api_key=api_key)
    print(tokman.get_token())


if __name__ == "__main__":
    sys.exit(main())
