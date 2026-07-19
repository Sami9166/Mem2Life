// 최상위 빌드 파일 — 하위 모듈에 공통 적용되는 플러그인만 선언한다.
plugins {
    alias(libs.plugins.android.application) apply false
    alias(libs.plugins.jetbrains.kotlin.android) apply false
    alias(libs.plugins.compose.compiler) apply false
}
