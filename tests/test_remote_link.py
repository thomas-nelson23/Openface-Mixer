"""The Send to host of the network remote: an address is used at once, a name is looked up
without holding up the window. No name server is asked: socket.getaddrinfo is a stand-in."""
import socket
import threading
import time
import unittest
from unittest import mock

try:
    from PySide6.QtWidgets import QApplication
except ImportError:
    QApplication = None

NAMES = {"desk.local": "10.0.0.7"}


@unittest.skipUnless(QApplication, "PySide6 is not installed")
class SendToTest(unittest.TestCase):
    def setUp(self):
        from openface_mixer import remote_link
        self.qt = QApplication.instance() or QApplication(["test"])
        self.answer = threading.Event()         # set: the name server answers
        self.answer.set()
        self.asked_on = []                      # the thread of each question to it
        patch = mock.patch.object(remote_link.socket, "getaddrinfo", self.getaddrinfo)
        patch.start()
        self.addCleanup(patch.stop)
        self.link = remote_link.RemoteLink(dict)
        self.addCleanup(self.link.deleteLater)
        self.addCleanup(self.answer.set)        # no lookup is left waiting

    def getaddrinfo(self, host, port, family=0, kind=0, proto=0, flags=0):
        if flags & socket.AI_NUMERICHOST:
            socket.inet_aton(host)              # OSError for anything but an address
            return [(family, kind, 17, "", (host, port))]
        self.asked_on.append(threading.current_thread())
        if not self.answer.wait(5):
            raise socket.gaierror("the test never let the name server answer")
        if host not in NAMES:
            raise socket.gaierror(socket.EAI_NONAME, "Name or service not known")
        return [(family, kind, 17, "", (NAMES[host], port))]

    def wait(self, done):
        end = time.monotonic() + 5
        while not done() and time.monotonic() < end:
            self.qt.processEvents()
            time.sleep(0.001)
        self.assertTrue(done())

    def lookups_over(self):
        for t in self.asked_on:
            t.join(5)
        self.qt.processEvents()

    def test_an_address_is_used_at_once(self):
        self.link.sent = {"/1/volume1": 0.5}
        self.link.set_target(" 192.168.1.20 ", 9001)
        self.assertEqual(self.link.target, ("192.168.1.20", 9001))
        self.assertEqual(self.link.sent, {})    # the new target is told everything
        self.assertEqual(self.asked_on, [])
        self.link.set_target("", 9001)
        self.assertIsNone(self.link.target)
        self.assertIsNone(self.link.target_error)

    def test_a_slow_name_server_does_not_hold_up_the_caller(self):
        self.answer.clear()
        self.link.set_target("desk.local", 9001)        # returns although nothing answers
        self.assertIsNone(self.link.target)
        self.assertIsNone(self.link.target_error)
        self.wait(lambda: self.asked_on)
        self.assertIsNot(self.asked_on[0], threading.main_thread())
        self.answer.set()
        self.wait(lambda: self.link.target is not None)
        self.assertEqual(self.link.target, ("10.0.0.7", 9001))

    def test_an_unknown_name_is_reported(self):
        self.link.set_target("nowhere.local", 9001)
        self.wait(lambda: self.link.target_error is not None)
        self.assertEqual(self.link.target_error, "nowhere.local")
        self.assertIsNone(self.link.target)
        self.link.set_target("10.0.0.9", 9001)
        self.assertIsNone(self.link.target_error)

    def test_an_answer_that_comes_after_the_host_was_changed_is_ignored(self):
        self.answer.clear()
        self.link.set_target("desk.local", 9001)
        self.wait(lambda: self.asked_on)
        self.link.set_target("10.0.0.9", 9001)
        self.answer.set()
        self.lookups_over()
        self.assertEqual(self.link.target, ("10.0.0.9", 9001))
        self.assertIsNone(self.link.target_error)

    def test_the_same_name_again_keeps_its_address_while_it_is_looked_up(self):
        self.link.set_target("desk.local", 9001)
        self.wait(lambda: self.link.target is not None)
        self.link.sent = {"/1/volume1": 0.5}
        self.answer.clear()
        self.link.set_target("desk.local", 9001)        # the settings were applied again
        self.assertEqual(self.link.target, ("10.0.0.7", 9001))
        self.answer.set()
        self.lookups_over()
        self.assertEqual(self.link.target, ("10.0.0.7", 9001))
        self.assertEqual(self.link.sent, {"/1/volume1": 0.5})     # nothing is sent twice


if __name__ == "__main__":
    unittest.main()
