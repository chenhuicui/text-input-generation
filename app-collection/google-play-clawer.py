import time

import uiautomator2 as u2


def crawl_google_play():
    # 连接模拟器
    print("==开始连接设备==")
    d = u2.connect("emulator-5554")
    print("**设备连接完成**")

    app_names = []
    max_scroll = 20

    for _ in range(max_scroll):
        # 通过 XPath 定位应用名称（需用 uiautomatorviewer 确认）
        elements = d.xpath('//android.view.View[contains(@content-desc, "Star rating:")]').all()
        print(len(elements))

        # 提取应用名称
        for elem in elements:
            content_desc = elem.attrib.get("content-desc", "")
            # 分割内容并取第一行（应用名称）
            if content_desc:
                app_name = content_desc.split("\n")[0]
                app_names.append(app_name)

        if len(set(app_names)) >= 100:
            break

        # 模拟向下滑动（根据屏幕分辨率调整坐标）
        d.swipe(500, 1500, 500, 200, 0.5)
        time.sleep(2)

    return set(app_names)


def save_to_txt(app_names: set):
    """将应用名称保存到 txt 文件"""
    try:

        with open(
                "/Users/cuichenhui/Documents/local-repositories/llm-empirical-study-workspace/llm-based-text-input-generation/src/main/scripts/finishedweather.txt",
                "w", encoding="utf-8") as f:
            for name in app_names:
                f.write(f"{name}\n")  # 每行一个应用名称
        print(f"成功保存 {len(app_names)} 条数据到.txt")
    except Exception as e:
        print(f"文件保存失败: {e}")


if __name__ == "__main__":
    apps = crawl_google_play()
    save_to_txt(apps)
    print(f"已保存 {len(apps)} 个应用到txt")
