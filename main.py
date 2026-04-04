# SPDX-License-Identifier: MIT
# Copyright (c) 2026 FairyClaw contributors, PKU DS Lab
import uvicorn

from fairyclaw.config.settings import settings


if __name__ == "__main__":
    uvicorn.run("fairyclaw.main:app", host=settings.host, port=settings.port, reload=False)
