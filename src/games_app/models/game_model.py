import uuid

from django.db import models

class GameModel(models.Model):
    id = models.CharField(max_length=64, primary_key=True, editable=False, unique=True, blank=False)
    status = models.IntegerField(default=0)
    created_by = models.CharField(max_length=64, blank=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_by = models.CharField(max_length=64, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    def save(self, *args, **kwargs):
        self.id = self.id or str(uuid.uuid4())
        super(Game, self).save(*args, **kwargs)

    def __str__(self):
        return f"Game {self.id} - Status: {self.status}"

    class Meta:
            db_table = 'Games'
