from typing import Annotated

from fastapi import Depends
from sqlalchemy.orm import Session

from src.db import get_session

DbSession = Annotated[Session, Depends(get_session)]
