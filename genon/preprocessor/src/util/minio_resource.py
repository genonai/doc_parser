from minio import Minio
import os
import time
import errno
import fcntl

from common.settings import minio_config
from common.logger import Logger

logger = Logger.getLogger(__name__)


class FileLock:
    def __init__(self, lock_path: str, timeout_sec: int = 600, poll_interval: float = 0.2):
        """
        Initialize a FileLock for coordinating exclusive access via a filesystem lock.
        
        Parameters:
            lock_path (str): Filesystem path of the lock file to create/use for exclusive locking.
            timeout_sec (int): Maximum number of seconds to wait for lock acquisition before timing out.
            poll_interval (float): Seconds to wait between acquisition retries while polling for the lock.
        """
        self.lock_path = lock_path
        self.timeout_sec = timeout_sec
        self.poll_interval = poll_interval
        self._fd = None

    def __enter__(self):
        """
        Acquire an exclusive, non-blocking filesystem lock on the configured lock file and return the FileLock instance.
        
        Ensures the lock file's parent directory exists and opens the lock file for appending. Repeatedly attempts to obtain a non-blocking exclusive lock until successful or the configured timeout is reached. When the lock is acquired, records the current PID and acquisition timestamp into the lock file.
        
        Returns:
            self: The FileLock instance with the lock held.
        
        Raises:
            TimeoutError: If the lock cannot be acquired within `self.timeout_sec`.
            OSError: For non-retryable filesystem errors encountered while attempting to acquire the lock.
        """
        os.makedirs(os.path.dirname(self.lock_path), exist_ok=True)
        self._fd = open(self.lock_path, "a+")

        start = time.time()
        while True:
            try:
                fcntl.flock(self._fd.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                self._fd.seek(0)
                self._fd.truncate()
                self._fd.write(f"pid={os.getpid()} acquired_at={time.time()}\n")
                self._fd.flush()
                return self
            except OSError as e:
                if e.errno not in (errno.EACCES, errno.EAGAIN):
                    raise
                if (time.time() - start) >= self.timeout_sec:
                    raise TimeoutError(f"Timed out acquiring lock: {self.lock_path}")
                time.sleep(self.poll_interval)

    def __exit__(self, exc_type, exc, tb):
        """
        Release the held filesystem lock and close the lock file.
        
        If a lock file is open, unlocks it, closes the file, and clears the internal file descriptor. Cleanup is performed in a finally block so the file is closed and the descriptor cleared even if unlocking raises.
        """
        try:
            if self._fd:
                fcntl.flock(self._fd.fileno(), fcntl.LOCK_UN)
        finally:
            if self._fd:
                self._fd.close()
                self._fd = None


def download_resource_files(bucket_name: str, resource_id: int, path: str):
    """
    Download all non-directory objects under the MinIO prefix "{resource_id}/resource" from the given bucket into the local directory `path`, using a filesystem lock to prevent concurrent downloads to the same `path`.
    
    Parameters:
        bucket_name (str): Name of the MinIO bucket to list objects from.
        resource_id (int): Resource identifier used to build the object prefix "{resource_id}/resource".
        path (str): Destination directory where objects will be written; directory structure of object keys is preserved.
    
    Notes:
        - Existing files in `path` are skipped.
        - A lock file named ".download_resource_files.lock" is created inside `path` to serialize concurrent calls.
        - Any exception raised during listing or downloading is logged and re-raised.
    """
    os.makedirs(path, exist_ok=True)

    lock_file = os.path.join(path, ".download_resource_files.lock")

    with FileLock(lock_file, timeout_sec=3600):
        logger.info(f'Acquired lock: {lock_file} (pid={os.getpid()})')

        minio_client = Minio(
            endpoint=minio_config.MINIO_ENDPOINT,
            access_key=minio_config.MINIO_ACCESS_KEY,
            secret_key=minio_config.MINIO_SECRET_KEY,
            secure=False
        )

        prefix = f"{resource_id}/resource"
        objects = list(minio_client.list_objects(bucket_name, prefix=prefix, recursive=True))

        try:
            logger.info(f'Downloading {len(objects)} resource files for {bucket_name} {resource_id}')

            for i, obj in enumerate(objects):
                if obj.is_dir:
                    continue

                rel_path = obj.object_name[len(prefix):].lstrip("/\\")
                if not rel_path:
                    continue

                destination_file = os.path.join(path, rel_path)
                os.makedirs(os.path.dirname(destination_file), exist_ok=True)

                if os.path.exists(destination_file):
                    logger.info(
                        f'[SKIP {i+1}/{len(objects)}] "{destination_file}" already exists'
                    )
                    continue

                logger.info(
                    f'Downloading [{i+1}/{len(objects)}] "{obj.object_name}" '
                    f'to "{destination_file}"...'
                )

                minio_client.fget_object(
                    bucket_name=bucket_name,
                    object_name=obj.object_name,
                    file_path=destination_file
                )

            logger.info('Completed!')
        except Exception as e:
            logger.error(f'Failed to download resource files: {e}')
            raise
        finally:
            logger.info(f'Releasing lock: {lock_file} (pid={os.getpid()})')
