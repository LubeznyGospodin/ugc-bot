from aiogram import Router

from . import admin, brands, profile, registration, start

router = Router(name="root")
# Порядок важен: start должен успеть обработать /start и текстовые кнопки
# раньше generic-обработчиков регистрации, поэтому подключаем его первым.
router.include_router(start.router)
router.include_router(profile.router)
router.include_router(brands.router)
router.include_router(admin.router)
router.include_router(registration.router)
