"""Delivery queue projection tests that do not require a running database."""
import os
import unittest
from types import SimpleNamespace as Row

os.environ.setdefault('SECRET_KEY', 'local-tests-only')
os.environ.setdefault('DATABASE_URL', 'postgresql+asyncpg://test:test@localhost/test')

from routers.tracking import group_outbound_delivery


class OutboundProjectionTests(unittest.TestCase):
    def setUp(self):
        self.media=Row(id=7,title='Fixture Film',poster_path='/poster.jpg')
        self.stremio=Row(id=1,name='Stremio')
        self.nuvio=Row(id=2,name='Nuvio')

    def test_one_title_contains_each_unresolved_service_and_its_error(self):
        stream=[
            (Row(state='pending',attempts=1,last_error=None),self.media,self.stremio),
            (Row(state='pending',attempts=2,last_error='TimeoutError'),self.media,self.nuvio),
            (Row(state='conflict',attempts=3,last_error='Playback changed'),self.media,self.nuvio),
        ]
        cloud=[(Row(provider='trakt',state='pending',attempts=1,last_error=None),self.media)]
        marker=Row(pending_connections=['connection:1','connection:2','trakt'])
        result=group_outbound_delivery(stream,cloud,[],{7:marker},{1:'Stremio',2:'Nuvio'})
        self.assertEqual(len(result),1)
        self.assertEqual(result[0]['title'],'Fixture Film')
        self.assertEqual(result[0]['state'],'conflict')
        self.assertEqual({item['connection'] for item in result[0]['deliveries']},{'Stremio','Nuvio','Trakt'})
        nuvio=next(item for item in result[0]['deliveries'] if item['connection']=='Nuvio')
        self.assertEqual(nuvio['attempts'],3)
        self.assertEqual(nuvio['error'],'Playback changed')

    def test_review_follows_remaining_marker_and_resolved_review_disappears(self):
        review=Row(media_id=7,state='pending')
        marker=Row(pending_connections=['connection:2'])
        pending=group_outbound_delivery([],[],[(review,self.media)],{7:marker},{2:'Nuvio'})
        self.assertEqual(len(pending),1)
        self.assertEqual([item['connection'] for item in pending[0]['deliveries']],['Nuvio'])
        marker.pending_connections=[]
        self.assertEqual(group_outbound_delivery([],[],[(review,self.media)],{7:marker},{2:'Nuvio'}),[])
        review.state='confirmed'
        marker.pending_connections=['connection:2']
        self.assertEqual(group_outbound_delivery([],[],[(review,self.media)],{7:marker},{2:'Nuvio'}),[])


if __name__ == '__main__':
    unittest.main()
