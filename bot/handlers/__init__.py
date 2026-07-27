from aiogram import Router

from . import admin, brands, papkids, placement, profile, registration, single, start, works

router = Router(name="root")
# Порядок важен: start должен успеть обработать /start и текстовые кнопки
# раньше generic-обработчиков регистрации, поэтому подключаем его первым.
router.include_router(start.router)
router.include_router(profile.router)
router.include_router(brands.router)
router.include_router(single.router)
router.include_router(works.router)
router.include_router(placement.router)
router.include_router(papkids.router)
router.include_router(admin.router)
router.include_router(registration.router)
