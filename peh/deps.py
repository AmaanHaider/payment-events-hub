from typing import Annotated

from fastapi import Depends
from sqlalchemy.orm import Session

from peh.db import get_session

DbSession = Annotated[Session, Depends(get_session)]
