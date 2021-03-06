import ast
import os

import mysql.connector as mariadb
import requests
from datetime import datetime
from flask import Flask, render_template, flash, abort
from flask import redirect
from flask import request
from flask import url_for
from flask_httpauth import HTTPBasicAuth
from tree import build_tree, sort_tree

from config import db_host, db_port, db_user, db_password, db_name, users, api_access_token_value, secret_key, api_version

app = Flask(__name__)
auth = HTTPBasicAuth()
app.secret_key = secret_key

api_url = 'https://graph.facebook.com/{}/'.format(api_version)
api_access_token_name = 'access_token'
api_post_field_name = 'fields'
api_post_field_value = 'source,full_picture,link'

payload = {api_access_token_name: api_access_token_value, api_post_field_name: api_post_field_value}

query_posts_classified = 'SELECT count(distinct p.post_id), count(distinct c.post_id), round(count(distinct c.post_id)/count(distinct p.post_id)*100,2) FROM post_has_phase p LEFT JOIN category c on (p.post_id = c.post_id) WHERE c.user IS NULL OR c.user NOT IN ("ben","max")'


@auth.get_password
def get_pw(username):
    if username in users:
        return users.get(username)
    return None


@app.route('/')
def main():
    total = 0
    current = 0
    percent = 0
    try:
        mariadb_connection = get_db_connection()
        cursor = mariadb_connection.cursor(buffered=True)
        cursor.execute(
            query_posts_classified)
        if cursor.rowcount != 0:
            row = cursor.fetchone()
            total = row[0]
            current = row[1]
            percent = row[2]

        statistic = {"total": total,
                     "current": current,
                     "percent": percent}
        return render_template('index.html', statistic=statistic)
    finally:
        # close database connection
        mariadb_connection.close()


@app.route('/statistics')
@auth.login_required
def statistics():
    total = 0
    current = 0
    percent = 0
    current_user = 0
    try:
        mariadb_connection = get_db_connection()
        cursor = mariadb_connection.cursor(buffered=True)
        cursor.execute(
            query_posts_classified)
        if cursor.rowcount != 0:
            row = cursor.fetchone()
            total = row[0]
            current = row[1]
            percent = row[2]

        cursor.execute(
            'SELECT count(distinct c.post_id) FROM post p LEFT JOIN category c on (p.id = c.post_id) WHERE c.user = %s', (auth.username(),))
        if cursor.rowcount != 0:
            row = cursor.fetchone()
            current_user = row[0]

        statistic = {"total": total,
                     "current": current,
                     "percent": percent,
                     "current_user": current_user,
                     "username": auth.username()}
        return render_template('statistics.html', statistic=statistic)
    finally:
        # close database connection
        mariadb_connection.close()


@app.route('/help')
def help():
    return render_template('help.html')


@app.route('/phase/<phase_id>/generate')
@auth.login_required
def generate(phase_id):
    try:
        mariadb_connection = get_db_connection()
        cursor = mariadb_connection.cursor(buffered=True)

        if int(phase_id) == 0:
            cursor.execute('SELECT post_id FROM post_has_phase WHERE phase_id = 0 AND post_id '
                           'NOT IN (SELECT post_id FROM category) AND '
                           'NOT EXISTS (SELECT post_id FROM post_has_phase '
                           'WHERE phase_id = 2 AND post_id '
                           'NOT IN (SELECT post_id FROM category WHERE user = %s)) ORDER BY rand() LIMIT 1',
                           (auth.username(),))
        else:
            cursor.execute('SELECT id FROM post WHERE id NOT IN (SELECT post_id FROM category ' +
                           'WHERE user = %s) ' +
                           'AND id IN (SELECT post_id FROM post_has_phase ' +
                           'WHERE phase_id = %s) ' +
                           'ORDER BY rand() LIMIT 1', (auth.username(), str(phase_id)))

        if cursor.rowcount == 0:
            if int(phase_id) == 0:
                # to begin with phase 3, the user has to has no incomplete posts in phase 2
                cursor.execute('SELECT post_id FROM post_has_phase WHERE phase_id = %s '
                               'AND post_id NOT IN '
                               '(SELECT post_id FROM category WHERE user = %s);', (str(2), auth.username()))
                if cursor.rowcount > 0:
                    return render_template('incomplete.html', phase=int(3), last_phase=int(2))
            else:
                return render_template('alldone.html', phase_id=int(phase_id))
        else:
            rows = cursor.fetchone()
            post_id = rows[0]
            return redirect(url_for('getpost', phase_id=int(phase_id), post_id=post_id))
    finally:
        # close database connection
        mariadb_connection.close()


@app.route('/phase/<phase_id>/post/<post_id>')
@auth.login_required
def getpost(phase_id, post_id):
    try:
        # open database connection
        mariadb_connection = get_db_connection()

        # get post info from the database
        cursor = mariadb_connection.cursor(buffered=True)
        cursor.execute(
            'SELECT p.text,p.num_likes,p.num_shares,p.num_angry,p.num_haha,p.num_wow,p.num_love,p.num_sad,p.name,p.type,p.picture,p.source,p.permanent_link,p.date,p.paid,pg.owner FROM post p JOIN page pg ON (p.page_id = pg.id) WHERE p.id = %s',
            (post_id,))
        if cursor.rowcount == 0 or cursor.rowcount > 1:
            abort(404)
        row = cursor.fetchall()[0]

        type = row[9].upper()
        picture = row[10]
        source = row[11]
        post_date = row[13]
        link = None

        r = requests.get(api_url + post_id, params=payload)

        # retrieve new video or picture url as these expire after some time
        if type in ['VIDEO', 'PHOTO', 'LINK']:
            source = r.json().get('source')
            picture = r.json().get('full_picture')

            if source is not None:
                # fix youtube urls
                if "youtube" in source:
                    source = source.replace("youtube.com/v/", "youtube.com/embed/")

                try:
                    # check if url is redirected
                    response = requests.head(source, allow_redirects=True)

                    # url is redirected if history exists
                    if response.history:
                        source = response.url
                except:
                    print("Error while looking for redirects.")
        if type in ['LINK']:
            link = r.json().get('link')

        post = {'text': row[0], 'num_likes': row[1], 'num_shares': row[2], 'num_angry': row[3], 'num_haha': row[4],
                'num_wow': row[5], 'num_love': row[6], 'num_sad': row[7], 'name': row[8], 'type': type,
                'picture': picture, 'source': source, 'perm_link': row[12], 'date': post_date, 'paid': row[14],
                'owner': row[15], 'id': post_id, 'link': link}
        cursor.execute('SELECT text, id, parent_id, date from comment where post_id = %s', (post_id,))
        # add comments
        post['comments'] = []
        comments = cursor.fetchall()

        tree = build_tree(comments)
        sort_tree(tree)

        post['comments'] = tree

        post['num_comments'] = len(post['comments'])
        cursor.execute('SELECT id, name FROM category_name')
        category_names = cursor.fetchall()

        # reactions became globally active on february the 24th in 2016
        reactions_available = post_date >= datetime.strptime('2016-02-24', "%Y-%m-%d")
        work_time = datetime.now()
        info = {"reactions_available": reactions_available,
                "work_time": work_time}

        # return post page
        return render_template('post.html', post=post, category_names=category_names, info=info, phase_id=phase_id)
    finally:
        # close database connection
        mariadb_connection.close()


@app.route('/update', methods=['POST'])
@auth.login_required
def update():
    try:
        connection = get_db_connection()
        cursor = connection.cursor(buffered=True)

        # Read form from request
        cat = request.form.getlist("category", None)
        succ = request.form.get('success', None)
        id = request.form["post_id"]
        phase_id = request.form["phase_id"]
        duration_seconds = (
            datetime.now() - datetime.strptime(request.form["work_time"], "%Y-%m-%d %H:%M:%S.%f")).total_seconds()

        # Build statements
        stmt = "REPLACE INTO category(user, post_id, successful, duration_seconds) VALUES(%s, %s, %s, %s)"
        stmt2 = "DELETE FROM category_has_category_name WHERE user = %s AND post_id = %s"
        stmt3 = "INSERT INTO category_has_category_name(user, post_id, category_name_id) VALUES(%s, %s, %s)"

        # Update Record in Database
        print('Updating record ' + str(id))
        cursor.execute(stmt, (auth.username(), id, succ, duration_seconds))
        cursor.execute(stmt2, (auth.username(), id))
        for category_id in cat:
            cursor.execute(stmt3, (auth.username(), id, category_id))
        connection.commit()

        # Return to generate page for a new post
        return generate(phase_id)
    finally:
        # close database connection
        connection.close()


@app.route('/skip', methods=['POST'])
@auth.login_required
def skip():
    try:
        connection = get_db_connection()
        cursor = connection.cursor(buffered=True)

        # Read form from request
        id = request.form["post_id"]
        phase_id = request.form["phase_id"]
        duration_seconds = (
            datetime.now() - datetime.strptime(request.form["work_time"], "%Y-%m-%d %H:%M:%S.%f")).total_seconds()

        # Build statements
        stmt = "REPLACE INTO skip(user, post_id, duration_seconds) VALUES(%s, %s, %s)"

        # Update Record in Database
        print('Skipping post ' + str(id))
        cursor.execute(stmt, (auth.username(), id, duration_seconds))
        connection.commit()

        # Return to generate page for a new post
        return generate(phase_id)
    finally:
        # close database connection
        connection.close()


# Private getter to create a connection object
def get_db_connection():
    return mariadb.connect(host=db_host, port=db_port, user=db_user, password=db_password,
                           database=db_name)


if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)  # NOSONAR
