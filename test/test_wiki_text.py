import logging
import pytest
import transaction
import zerodb
from zerodb.models import Model, fields
from conftest import do_zeo_server
from db import WikiPage, TEST_PASSPHRASE

logging.basicConfig(level=logging.DEBUG)


class Page(Model):
    title = fields.Field()
    text = fields.TextNew()


@pytest.fixture(scope="module")
def many_server(request, pass_file, tempdir):
    sock = do_zeo_server(request, pass_file, tempdir)
    db = zerodb.DB(sock, username="root", password=TEST_PASSPHRASE, debug=True)
    with transaction.manager:
        for i in range(2000):
            db.add(Page(title="hello %s" % i, text="lorem ipsum dolor sit amet" * 2))
        for i in range(1000):
            # Variable length while keeping number of terms the same
            # will cause variable scores
            db.add(Page(title="hello %s" % i, text="this is something we're looking for" * int(i ** 0.5)))
        db.add(Page(title="extra page", text="something else is here"))
    db.disconnect()
    return sock


@pytest.fixture(scope="module")
def manydb(request, many_server):
    zdb = zerodb.DB(many_server, username="root", password=TEST_PASSPHRASE, debug=True)

    @request.addfinalizer
    def fin():
        zdb.disconnect()  # I suppose, it's not really required

    return zdb


def get_one(db):
    key = db[WikiPage]._objects.tree.keys()[0]
    doc = db[WikiPage]._objects[key]
    return key, doc


def get_cat(db):
    return db[WikiPage]._catalog['text']


def test_indexed(wiki_db):
    # The DB is indexed and loaded
    assert len(wiki_db[WikiPage]) == get_cat(wiki_db).documentCount()


def test_reindex(wiki_db):
    with transaction.manager:
        key, test_doc = get_one(wiki_db)
        original_size = len(test_doc.text)
        test_doc.text += "\nTestWord to change the text."
        get_cat(wiki_db).reindex_doc(key, test_doc)

    with transaction.manager:
        test_doc = wiki_db[WikiPage]._objects[key]
        test_doc.text = test_doc.text[:original_size] + "\nNewTestWord to change the text."
        get_cat(wiki_db).reindex_doc(key, test_doc)


def test_unindex(wiki_db):
    with transaction.manager:
        cat = get_cat(wiki_db)
        key, _ = get_one(wiki_db)
        cat.unindex_doc(key)
        cat.unindex_doc(-99)  # No such ID, should be OK


def test_idf2(wiki_db):
    with transaction.manager:
        text = "Africa Asia NonExistingWord"
        index = get_cat(wiki_db).index
        wids = index._lexicon.sourceToWordIds(text)
        assert len(wids) == 3
        wids = index._remove_oov_wids(wids)
        assert len(wids) == 2
        idfs = map(index.idf2, wids)
        assert all(idf > 0 for idf in idfs)


def test_query_weight(wiki_db):
    index = get_cat(wiki_db).index
    assert index.query_weight("Africa Asia SomethingWhichIsNotThere") > 0


def test_search_wids(wiki_db):
    index = get_cat(wiki_db).index
    text = "Africa Asia"
    wids = index._lexicon.sourceToWordIds(text)
    for wordinfo, idf in index._search_wids(wids):
        assert idf > 0
        weight, docid = iter(wordinfo).next()
        assert weight < 0
        assert docid >= 0


def test_search(wiki_db):
    index = get_cat(wiki_db).index
    assert list(index.search("")) == []
    assert len(list(index.search("Africa"))) > 0
    assert len(list(index.search("Australia rugby"))) > 0
    assert len(list(index.search_glob("Austral*"))) > 0
    assert len(list(index.search_glob("itisnotthere*"))) == 0


def test_search_many(manydb):
    index = manydb[Page]._catalog["text"].index
    it = index.search("something looking")
    ids = [x[0] for x in it]
    assert len(ids) == 1000
    # Longer docs for this query and our synthetic docs have higjer score
    lens = [len(manydb[Page]._objects[i].text) for i in ids]
    assert lens == sorted(lens, reverse=True)
