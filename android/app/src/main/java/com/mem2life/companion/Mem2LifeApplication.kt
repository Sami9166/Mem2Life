package com.mem2life.companion

import android.app.Application

/**
 * Vuzix Blade 2 온글래스 앱의 Application.
 *
 * 표준 Android API만 사용하므로 별도 SDK 초기화가 없다 — 과거 Meta DAT SDK의
 * Wearables.initialize() 호출은 Blade 2 전환으로 제거됐다. 앱 전역 초기화가
 * 필요해지면 여기에 추가한다.
 */
class Mem2LifeApplication : Application()
