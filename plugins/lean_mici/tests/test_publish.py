import mmap
import tempfile

from ..worker import ENVELOPE, OFFSET, SIZE, publish


def test_publish_commits_length_last():
  with tempfile.TemporaryFile() as f:
    f.truncate(SIZE)
    with mmap.mmap(f.fileno(), SIZE) as mailbox:
      mailbox[OFFSET:] = b"x" * (SIZE - OFFSET)
      snapshots = []

      class ObservedMailbox:
        def __setitem__(self, key, value):
          mailbox[key] = value
          snapshots.append(bytes(mailbox[OFFSET:]))

      data = b"reply" * 1664
      publish(f.fileno(), ObservedMailbox(), 11, 22, 33, 3, data)
      assert ENVELOPE.unpack_from(snapshots[0])[-1] == 0
      assert ENVELOPE.unpack_from(snapshots[-2]) == (11, 22, 33, 3, 0)
      assert ENVELOPE.unpack_from(snapshots[-1]) == (11, 22, 33, 3, len(data))
      assert snapshots[-1][ENVELOPE.size:ENVELOPE.size + len(data)] == data
      assert snapshots[-1][ENVELOPE.size + len(data):] == b"x" * (SIZE - OFFSET - ENVELOPE.size - len(data))
