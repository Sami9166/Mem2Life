// Mem2Life Vuzix Blade 2 온글래스 앱 — Gradle 설정
//
// Vuzix Blade 2는 Android 11(API 30)이 탑재된 독립 실행형 기기로, 표준 Android
// API(Camera2, AudioRecord 등)만으로 개발한다 — 별도의 사설 SDK 저장소나
// 인증 토큰이 필요 없다. (과거 Meta DAT SDK 시절의 GitHub Packages 저장소와
// 토큰 로딩 로직은 Vuzix Blade 2 전환과 함께 제거됐다.)

pluginManagement {
    repositories {
        google {
            content {
                includeGroupByRegex("com\\.android.*")
                includeGroupByRegex("com\\.google.*")
                includeGroupByRegex("androidx.*")
            }
        }
        mavenCentral()
        gradlePluginPortal()
    }
}

dependencyResolutionManagement {
    repositoriesMode.set(RepositoriesMode.FAIL_ON_PROJECT_REPOS)
    repositories {
        google()
        mavenCentral()
    }
}

rootProject.name = "Mem2LifeCompanion"

include(":app")
