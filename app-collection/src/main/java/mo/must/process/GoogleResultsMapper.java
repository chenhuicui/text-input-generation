package mo.must.process;

import java.util.List;

/**
 * @Description: TODO
 * @Author: xiaoche
 * @Date: 2025/5/24 10:58
 */
public interface GoogleResultsMapper {
    void insertOrUpdate(GoogleResults results);
    List<GoogleResults> selectByAppId(String appId);
}
