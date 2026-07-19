// Mem2Life Android 컴패니언 앱 — Gradle 설정
//
// Meta Wearables DAT SDK는 GitHub Packages로 배포된다. 저장소 접근에는
// read:packages 스코프의 GitHub 개인 액세스 토큰이 필요하다 (환경변수
// GITHUB_TOKEN 또는 local.properties의 github_token 키로 제공).
// 절차는 https://github.com/facebook/meta-wearables-dat-android 참고.

import java.util.Properties
import kotlin.io.path.div
import kotlin.io.path.exists
import kotlin.io.path.inputStream

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

val localProperties =
    Properties().apply {
        val localPropertiesPath = rootDir.toPath() / "local.properties"
        if (localPropertiesPath.exists()) {
            load(localPropertiesPath.inputStream())
        }
    }

dependencyResolutionManagement {
    repositoriesMode.set(RepositoriesMode.FAIL_ON_PROJECT_REPOS)
    repositories {
        google()
        mavenCentral()
        maven {
            url = uri("https://maven.pkg.github.com/facebook/meta-wearables-dat-android")
            credentials {
                username = "" // 사용하지 않음
                password = System.getenv("GITHUB_TOKEN") ?: localProperties.getProperty("github_token")
            }
        }
    }
}

rootProject.name = "Mem2LifeCompanion"

include(":app")
