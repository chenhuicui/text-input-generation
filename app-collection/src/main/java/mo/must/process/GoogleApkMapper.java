package mo.must.process;

import java.util.List;

public interface GoogleApkMapper {
    void insertOrUpdate(GoogleApk apk);
    void deleteByAppId(String appId);
    GoogleApk selectByAppId(String appId);
    List<GoogleApk> selectAll();
    GoogleApk getOnlyGoogleApk();

    List<String> selectAppIdList();
}
