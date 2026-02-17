import random
import time


class BaseScraper:
    @staticmethod
    def random_wait(repeat: int = 1):
        for _ in range(repeat):
            # wait a random amount of seconds between 0.5 and 1.5
            time.sleep(random.uniform(0.3, 1.3))
