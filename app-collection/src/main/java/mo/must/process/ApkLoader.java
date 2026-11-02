package mo.must.process;

import cn.hutool.core.collection.CollectionUtil;
import mo.must.process.domain.ApkVariantInfoRsp;
import mo.must.process.domain.SearchApkLastestVersionRsp;
import mo.must.process.domain.SearchApkRsp;
import org.apache.commons.lang3.StringUtils;
import org.jsoup.Jsoup;
import org.jsoup.nodes.Document;
import org.jsoup.nodes.Element;
import org.jsoup.select.Elements;
import org.openqa.selenium.By;
import org.openqa.selenium.WebDriver;
import org.openqa.selenium.chrome.ChromeDriver;
import org.openqa.selenium.chrome.ChromeOptions;
import org.openqa.selenium.support.ui.ExpectedConditions;
import org.openqa.selenium.support.ui.WebDriverWait;

import java.io.File;
import java.net.URLEncoder;
import java.nio.charset.StandardCharsets;
import java.time.*;
import java.time.format.DateTimeFormatter;
import java.util.*;
import java.util.stream.Collectors;

/**
 * @Description: TODO
 * @Author: xiaoche
 * @Date: 2025/4/15 00:09
 */
public class ApkLoader {
    public static final String CHROME_DRIVER_PATH = "/Users/xiaoche/king/driver/135.0.7049.84/chromedriver";
    public static final String OUTPUT_DIR = "/Users/xiaoche/king/codeworkspace/mustworkspace/llm-based-text-input-generation/apk/";

    public static void main(String[] args) throws Exception {
        dealApk();
    }

    public static void dealApk() {
        while (true) {
            try (var session = MyBatisUtil.getSession()) {
                GoogleApkMapper mapper = session.getMapper(GoogleApkMapper.class);

                while (true) {
                    GoogleApk onlyGoogleApk = null;
                    try {
                        onlyGoogleApk = mapper.getOnlyGoogleApk();
                        if (onlyGoogleApk == null) {
                            System.out.println("🎉 没有待处理的记录了！");
                            return;
                        }

                        System.out.println("开始加载。。。。");
                        System.out.println("1、开始检索：" + onlyGoogleApk.getAppId());

                        SearchApkRsp searchApkRsp = dealSearchApk(onlyGoogleApk.getAppId());
                        if (searchApkRsp == null) {
                            onlyGoogleApk.setVersionCode("-");
                            mapper.insertOrUpdate(onlyGoogleApk);
                            session.commit();
                            continue;
                        }

                        onlyGoogleApk.setVersionCode(searchApkRsp.getVersionName());
                        System.out.println("2、检索完成");

                        List<SearchApkLastestVersionRsp> versionRsps = searchApkRsp.getSearchApkLastestVersionRsps();
                        if (!CollectionUtil.isEmpty(versionRsps)) {
                            SearchApkLastestVersionRsp versionRsp = versionRsps.get(0);
                            onlyGoogleApk.setUpdateDate(versionRsp.getUpdateTime())
                                    .setMinAndroid(versionRsp.getMinAndroidVersion());

                            System.out.println("3、加载app最新版本信息");

                            String lastest = dealApkLastestVersion(versionRsp.getUrl());
                            if (StringUtils.isBlank(lastest)) {
                                System.out.println("3.1、加载最新版本信息失败");
                                onlyGoogleApk.setVersionCode("--");
                                mapper.insertOrUpdate(onlyGoogleApk);
                                session.commit();
                                continue;
                            }

                            List<ApkVariantInfoRsp> variants = parseApkVariantsFromHtml(lastest);

                            String arch = "arm64-v8a";
                            boolean found = false;
                            for (ApkVariantInfoRsp info : variants) {
                                if (info.getArch().equalsIgnoreCase(arch)) {
                                    System.out.println("4、开始下载apk");
                                    onlyGoogleApk.setApkFlag(info.getType().equalsIgnoreCase("APK") ? 1 : 0)
                                            .setDownloadUrl(info.downloadUrl)
                                            .setApkDownloadDate(new Date());
                                    mapper.insertOrUpdate(onlyGoogleApk);
                                    session.commit();

                                    String apkName = downloadApk(info, OUTPUT_DIR, 1 * 60 * 60);
                                    onlyGoogleApk.setApkName(apkName);
                                    mapper.insertOrUpdate(onlyGoogleApk);
                                    session.commit();
                                    found = true;
                                    break;
                                }
                            }

                            if (!found) {
                                System.out.println("4.1、未匹配到对应版本");
                                onlyGoogleApk.setVersionCode("--");
                                mapper.insertOrUpdate(onlyGoogleApk);
                                session.commit();
                            }

                        } else {
                            onlyGoogleApk.setVersionCode("-");
                            mapper.insertOrUpdate(onlyGoogleApk);
                            session.commit();
                        }

                    } catch (Exception e) {
                        System.err.println("❌ 单条处理失败: " + (onlyGoogleApk != null ? onlyGoogleApk.getAppId() : "未知"));
                        e.printStackTrace();

                        // 标记当前记录失败，避免死循环
                        if (onlyGoogleApk != null) {
                            try {
                                onlyGoogleApk.setVersionCode("---");
                                mapper.insertOrUpdate(onlyGoogleApk);
                                session.commit();
                            } catch (Exception ex2) {
                                System.err.println("⚠️ 错误记录失败：" + ex2.getMessage());
                            }
                        }
                        Thread.sleep(1000); // 防抖
                        break;
                    }
                }
            } catch (Exception e) {
                System.err.println("🌋 数据库连接层异常，将自动重试！");
                e.printStackTrace();
                try {
                    Thread.sleep(5000);
                } catch (InterruptedException ignored) {}
            }
        }
    }

    public static String downloadApk(ApkVariantInfoRsp variant, String downloadDir, int timeoutSeconds) throws Exception {
        System.setProperty("webdriver.chrome.driver", CHROME_DRIVER_PATH);
        Map<String, Object> prefs = new HashMap<>();
        prefs.put("download.prompt_for_download", false);
        prefs.put("download.default_directory", downloadDir);
        prefs.put("safebrowsing.enabled", true);
        ChromeOptions options = new ChromeOptions();
        options.setExperimentalOption("prefs", prefs);
        // options.addArguments("--headless=new"); // 可选：无头模式
        WebDriver driver = new ChromeDriver(options);
        try {
            File dir = new File(downloadDir);
            if (!dir.exists()) dir.mkdirs();
            File[] beforeFiles = dir.listFiles();
            String downloadUrl = variant.getDownloadUrl().replace("&amp;", "&");
            System.out.println("打开下载链接: " + downloadUrl);
            driver.get(downloadUrl);
            long expectedBytes = parseSizeToBytes(variant.getSize());
            File downloadedFile = waitForFileBySize(downloadDir, beforeFiles, expectedBytes, timeoutSeconds);
            if (downloadedFile != null) {
                String absolutePath = downloadedFile.getAbsolutePath();
                String fileName = absolutePath.substring(absolutePath.lastIndexOf("/") + 1);
                System.out.println("✅ 下载完成: " + absolutePath);
                return fileName;
            } else {
                System.err.println("❌ 下载失败或超时，未检测到符合大小的文件。");
            }
        } finally {
            driver.quit();
            System.out.println("浏览器已关闭");
        }
        return null;
    }

    private static long parseSizeToBytes(String sizeText) {
        sizeText = sizeText.trim().toUpperCase();
        try {
            if (sizeText.endsWith("MB")) {
                return (long) (Double.parseDouble(sizeText.replace("MB", "").trim()) * 1024 * 1024);
            } else if (sizeText.endsWith("KB")) {
                return (long) (Double.parseDouble(sizeText.replace("KB", "").trim()) * 1024);
            } else if (sizeText.endsWith("GB")) {
                return (long) (Double.parseDouble(sizeText.replace("GB", "").trim()) * 1024 * 1024 * 1024);
            }
        } catch (Exception ignored) {
        }
        return 0;
    }

    private static File waitForFileBySize(String dirPath, File[] beforeFiles, long expectedBytes, int timeoutSeconds) throws InterruptedException {
        long start = System.currentTimeMillis();
        while ((System.currentTimeMillis() - start) < timeoutSeconds * 1000L) {
            File[] currentFiles = new File(dirPath).listFiles();
            if (currentFiles == null) continue;

            for (File f : currentFiles) {
                boolean isNew = true;
                for (File old : beforeFiles) {
                    if (f.getName().equals(old.getName()) && f.length() == old.length()) {
                        isNew = false;
                        break;
                    }
                }
                if (isNew && !f.getName().endsWith(".crdownload") && f.length() >= expectedBytes * 0.95) {
                    return f;
                }
            }
            Thread.sleep(1000);
        }
        return null;
    }

    public static List<ApkVariantInfoRsp> parseApkVariantsFromHtml(String html) {
        List<ApkVariantInfoRsp> list = new ArrayList<>();
        Document doc = Jsoup.parse(html);

        // 支持两个tab：best-variant-tab 和 variants-tab
        for (String tabId : List.of("best-variant-tab", "variants-tab")) {
            Element tab = doc.getElementById(tabId);
            if (tab == null) continue;

            Elements treeItems = tab.select("div.tree > ul > li");
            for (Element treeItem : treeItems) {
                String arch = Optional.ofNullable(treeItem.selectFirst("code"))
                        .map(Element::text).orElse("unknown");

                // 多个架构用 , 分隔，如：arm64-v8a, armeabi-v7a
                List<String> archList = Arrays.stream(arch.split(","))
                        .map(String::trim).collect(Collectors.toList());

                Elements fileListItems = treeItem.select("ul.file-list > li > a.variant");
                for (Element a : fileListItems) {
                    String downloadUrl = a.attr("href").replace("&amp;", "&");

                    String vername = Optional.ofNullable(a.selectFirst("span.vername"))
                            .map(Element::text).orElse("");
                    String vercode = Optional.ofNullable(a.selectFirst("span.vercode"))
                            .map(Element::text).orElse("");
                    String type = Optional.ofNullable(a.selectFirst("span.vtype span"))
                            .map(Element::text).orElse("");

                    Elements specs = a.select("div.description span.spec");
                    String size = "", minSdk = "", dpi = "";
                    for (Element spec : specs) {
                        String t = spec.text().trim();
                        if (t.contains("MB") || t.contains("GB")) size = t;
                        else if (t.contains("Android")) minSdk = t;
                        else if (t.contains("dpi")) dpi = t;
                    }

                    for (String singleArch : archList) {
                        ApkVariantInfoRsp info = new ApkVariantInfoRsp();
                        info.setArch(singleArch);
                        info.setVersionName(vername);
                        info.setVersionCode(vercode);
                        info.setType(type);
                        info.setSize(size);
                        info.setMinSdk(minSdk);
                        info.setDpi(dpi);
                        info.setDownloadUrl(downloadUrl);
                        list.add(info);
                    }
                }
            }
        }

        return list;
    }

    private static String getTextSafe(Element parent, String selector) {
        Element el = parent != null ? parent.selectFirst(selector) : null;
        return el != null ? el.text().trim() : "";
    }

    public static String dealApkLastestVersion(String url) throws Exception {
        System.setProperty("webdriver.chrome.driver", CHROME_DRIVER_PATH);
        ChromeOptions options = new ChromeOptions();
        options.addArguments("--headless");
        options.addArguments("--disable-gpu");
        options.addArguments("--no-sandbox");
        options.addArguments("--lang=zh-CN");
        options.addArguments("user-agent=Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/134.0.0.0 Safari/537.36");
        WebDriver driver = new ChromeDriver(options);
        try {
            driver.get(url);
            // 等待 id 为 download-tab 的元素出现
            WebDriverWait wait = new WebDriverWait(driver, Duration.ofSeconds(10));
            wait.until(ExpectedConditions.visibilityOfElementLocated(By.id("download-tab")));
            return driver.getPageSource();
        } catch (Exception e) {
            e.printStackTrace();
            return null;
        } finally {
            driver.quit();
        }
    }



    public static SearchApkRsp dealSearchApk(String appId) throws Exception {
        System.setProperty("webdriver.chrome.driver", CHROME_DRIVER_PATH);
        ChromeOptions options = new ChromeOptions();
        options.addArguments("--disable-gpu");
        options.addArguments("--no-sandbox");
        // options.addArguments("--headless=new");
        WebDriver driver = new ChromeDriver(options);

        String searchUrl = "https://apkcombo.com/zh/search?q=" + URLEncoder.encode(appId, StandardCharsets.UTF_8);
        driver.get(searchUrl);
        Thread.sleep(2000);

        SearchApkRsp searchApkRsp = new SearchApkRsp();
        searchApkRsp.setAppId(appId).setUrl(searchUrl).setContent(driver.getPageSource());

        Document doc = Jsoup.parse(driver.getPageSource());

        Elements infoItems = doc.select("div.information-table > div.item");
        for (Element item : infoItems) {
            String name = item.selectFirst("div.name").text().trim();
            String value = item.selectFirst("div.value").text().trim();

            switch (name) {
                case "版本":
                    searchApkRsp.setVersionName(value.trim());
                    break;
                case "更新":
                    searchApkRsp.setUpdateDate(value);
                    break;
                case "开发者":
                    searchApkRsp.setDeveloper(value);
                    break;
                case "分类":
                    searchApkRsp.setCategory(value);
                    break;
                case "Google Play ID":
                    searchApkRsp.setPlayId(value);
                    break;
                case "安装次数":
                    searchApkRsp.setInstallCount(value);
                    break;
            }
        }

        List<SearchApkLastestVersionRsp> list = new ArrayList<>();
        Elements items = doc.select("ul.list-versions li");

        for (Element item : items) {
            SearchApkLastestVersionRsp rsp = new SearchApkLastestVersionRsp();
            Element link = item.selectFirst("a.ver-item");
            if (link != null) {
                rsp.setUrl("https://apkcombo.com" + link.attr("href"));
            }

            Element vername = item.selectFirst("span.vername");
            if (vername != null) {
                rsp.setVername(vername.text());
            }

            Element vtype = item.selectFirst("span.vtype span");
            if (vtype != null) {
                rsp.setVtype(vtype.text());
            }

            Element desc = item.selectFirst("div.description");
            if (desc != null) {
                rsp.setDescription(desc.text());
                // 解析时间与最低版本
                String descText = desc.text(); // 例如 "2025年4月1日 · Android 6.0+"
                String[] parts = descText.split("·");
                if (parts.length == 2) {
                    String datePart = parts[0].trim(); // "2025年4月1日"
                    String minVersion = parts[1].trim(); // "Android 6.0+"
                    Date parsedDate = parseChineseDateToDatetime(datePart);
                    rsp.setUpdateTime(parsedDate);
                    rsp.setMinAndroidVersion(minVersion);
                }
            }

            list.add(rsp);
        }

        searchApkRsp.setSearchApkLastestVersionRsps(list);
        driver.quit();
        return searchApkRsp;
    }

    private static Date parseChineseDateToDatetime(String chineseDate) {
        try {
            DateTimeFormatter inputFormatter = DateTimeFormatter.ofPattern("yyyy年M月d日", Locale.CHINA);
            LocalDate localDate = LocalDate.parse(chineseDate, inputFormatter);
            LocalDateTime localDateTime = localDate.atStartOfDay();
            ZoneId zoneId = ZoneId.systemDefault();
            Instant instant = localDateTime.atZone(zoneId).toInstant();
            return Date.from(instant);
        } catch (Exception e) {
            e.printStackTrace();
            return null;
        }
    }
}

