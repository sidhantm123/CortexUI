# Tests package
import gc
import shutil


def remove_dir(path):
    """
    Delete a test's temporary folder. Windows can't delete a file that is still open or
    memory-mapped (neo maps each .nsX until its reader is freed), so collect garbage first and
    leave a file that is still held to the system's temp cleanup.
    """
    gc.collect()
    shutil.rmtree(path, ignore_errors=True)

