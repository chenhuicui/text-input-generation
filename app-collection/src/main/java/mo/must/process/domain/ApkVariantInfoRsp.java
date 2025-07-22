package mo.must.process.domain;

import lombok.Data;
import lombok.experimental.Accessors;

@Data
@Accessors(chain = true)
public class ApkVariantInfoRsp {
    public String arch;        // 架构: arm64-v8a / armeabi-v7a
    public String versionName; // 版本名: Khan Kids 7.0.6
    public String versionCode; // 版本号: (111)
    public String type;        // 类型: XAPK
    public String size;        // 大小: 145 MB
    public String minSdk;      // 最低系统: Android 6.0+
    public String dpi;         // DPI 范围: 120 - 65534dpi
    public String downloadUrl; // 下载链接

}
