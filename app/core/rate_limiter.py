"""
Rate Limiting с использованием Redis
"""
import redis
import logging
from fastapi import HTTPException, Request
from app.config import settings

logger = logging.getLogger(__name__)

class RateLimiter:
    """Rate limiter на основе Redis"""
    
    def __init__(self):
        self._redis_client = None
        self.limit = settings.RATE_LIMIT_PER_HOUR
        self.window = settings.RATE_LIMIT_WINDOW
        self._redis_available = True
    
    @property
    def redis_client(self):
        """Lazy initialization of Redis client"""
        if self._redis_client is None and self._redis_available:
            try:
                self._redis_client = redis.from_url(settings.RATE_LIMIT_REDIS_URL, decode_responses=True)
                # Test connection
                self._redis_client.ping()
                logger.info("Redis connection established")
            except Exception as e:
                logger.error(f"Redis connection failed: {e}")
                self._redis_available = False
                self._redis_client = None
        return self._redis_client
    
    def _get_key(self, ip: str) -> str:
        """Генерирует ключ для Redis"""
        prefix = str(getattr(settings, "RATE_LIMIT_REDIS_PREFIX", "ratelimit") or "ratelimit").strip(": ")
        return f"{prefix}:legacy:{ip}"
    
    def check_rate_limit(self, ip: str) -> dict:
        """
        Проверяет rate limit для IP
        
        Returns:
            dict: {
                'allowed': bool,
                'remaining': int,
                'reset_in': int  # seconds
            }
        """
        # If Redis is not available, allow all requests
        if not self.redis_client:
            return {
                'allowed': True,
                'remaining': self.limit,
                'reset_in': self.window
            }
        
        key = self._get_key(ip)
        
        try:
            # Получаем текущее количество запросов
            current = self.redis_client.get(key)
            
            if current is None:
                # Первый запрос - устанавливаем счетчик
                pipe = self.redis_client.pipeline()
                pipe.setex(key, self.window, 1)
                pipe.execute()
                return {
                    'allowed': True,
                    'remaining': self.limit - 1,
                    'reset_in': self.window
                }
            
            current = int(current)
            
            if current >= self.limit:
                # Лимит исчерпан
                ttl = self.redis_client.ttl(key)
                return {
                    'allowed': False,
                    'remaining': 0,
                    'reset_in': max(0, ttl)
                }
            
            # Увеличиваем счетчик
            self.redis_client.incr(key)
            ttl = self.redis_client.ttl(key)
            
            return {
                'allowed': True,
                'remaining': self.limit - current - 1,
                'reset_in': max(0, ttl)
            }
        except Exception as e:
            logger.error(f"Error checking rate limit: {e}")
            # Allow request if Redis fails
            return {
                'allowed': True,
                'remaining': self.limit,
                'reset_in': self.window
            }
    
    def get_remaining(self, ip: str) -> int:
        """Возвращает оставшееся количество запросов"""
        if not self.redis_client:
            return self.limit
        
        try:
            key = self._get_key(ip)
            current = self.redis_client.get(key)
            if current is None:
                return self.limit
            return max(0, self.limit - int(current))
        except Exception as e:
            logger.error(f"Error getting remaining requests: {e}")
            return self.limit


# Singleton instance
rate_limiter = RateLimiter()


def check_rate_limit_http(request: Request):
    """
    Dependency для FastAPI - проверяет rate limit
    
    Raises:
        HTTPException: 429 Too Many Requests если лимит исчерпан
    """
    # Получаем IP клиента
    forwarded = request.headers.get("X-Forwarded-For")
    if forwarded:
        ip = forwarded.split(",")[0].strip()
    else:
        ip = request.client.host
    
    result = rate_limiter.check_rate_limit(ip)
    
    if not result['allowed']:
        minutes = result['reset_in'] // 60
        raise HTTPException(
            status_code=429,
            detail={
                "message": f"Rate limit exceeded. Try again in {minutes} minutes.",
                "reset_in": result['reset_in'],
                "limit": rate_limiter.limit
            }
        )
    
    return result
